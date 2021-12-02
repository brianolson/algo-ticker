#!/usr/bin/env python3
#
# pip install py-algorand-sdk

import argparse
import base64
import json
import logging
import os
import re
import signal
import sys
import time

import algosdk
from algosdk.v2client.algod import AlgodClient

logger = logging.getLogger(__name__)

def token_addr_from_algod(algorand_data):
    addr = open(os.path.join(algorand_data, 'algod.net'), 'rt').read().strip()
    if not addr.startswith('http'):
        addr = 'http://' + addr
    token = open(os.path.join(algorand_data, 'algod.token'), 'rt').read().strip()
    return token, addr

class Algobot:
    def __init__(self, algorand_data=None, token=None, addr=None, headers=None, block_handlers=None, txn_handlers=None, progress_log_path=None, prev_round=None):
        """
        algorand_data = path to algod data dir
        addr, token = algod URI and access token
        headers = dict of HTTP headers to send to algod
        block_handlers = list of callables
        txn_handlers = list of callables
        progress_log_path = path to file to record round progress; read on start to continue after last seen round
        prev_round = start with (prev_round + 1)
        """
        self.algorand_data = algorand_data
        self.token = token
        self.addr = addr
        self.headers = headers
        self._algod = None
        self.block_handlers = block_handlers or list()
        self.txn_handlers = txn_handlers or list()
        self.progress_log_path = progress_log_path
        self._progresslog = None
        self._progresslog_write_count = 0
        self.go = True
        self.prev_round = prev_round
        return

    def algod(self):
        "return an open algosdk.v2client.algod.AlgodClient"
        if self._algod is None:
            if self.algorand_data:
                token, addr = token_addr_from_algod(self.algorand_data)
            else:
                token = self.token
                addr = self.addr
            self._algod = AlgodClient(token, addr, headers=self.headers)
        return self._algod

    def nextblock(self, lastround=None, retries=30):
        trycount = 0
        while (trycount < retries) and self.go:
            trycount += 1
            try:
                return self._nextblock_inner(lastround)
            except Exception as e:
                if trycount >= retries:
                    logger.error('too many errors in nextblock retries')
                    raise
                else:
                    logger.warn('error in nextblock(%r) (retrying): %s', lastround, e)
                    self._algod = None # retry with a new connection
                    time.sleep(1.2)
        return None

    def _nextblock_inner(self, lastround):
        algod = self.algod()
        if lastround is None:
            status = algod.status()
            lastround = status['last-round']
            logger.debug('nextblock status last-round %s', lastround)
        else:
            try:
                blk = self.algod().block_info(lastround + 1)
                if blk:
                    return blk
                logger.warning('null block %d, lastround=%r', lastround+1, lastround)
            except:
                pass
        status = algod.status_after_block(lastround)
        nbr = status['last-round']
        retries = 30
        while (nbr > lastround + 1) and self.go:
            # try lastround+1 one last time
            try:
                blk = self.algod().block_info(lastround + 1)
                if blk:
                    return blk
                logger.warning('null block %d, lastround=%r, status.last-round=%d', lastround+1, lastround, nbr)
                time.sleep(1.1)
                retries -= 1
                if retries <= 0:
                    raise Exception("too many null block for %d", lastround+1)
            except:
                break
        blk = self.algod().block_info(nbr)
        if blk:
            return blk
        raise Exception('got None for blk {}'.format(nbr))

    def loop(self):
        """Start processing blocks and txns
        runs until error or bot.go=False
        """
        if self.prev_round is not None:
            lastround = self.prev_round
        else:
            lastround = self.recover_progress()
        try:
            self._loop_inner(lastround)
        finally:
            self.close()

    def _loop_inner(self, lastround):
        while self.go:
            b = self.nextblock(lastround)
            if b is None:
                print("got None nextblock. exiting")
                return
            nowround = blockround(b)
            if (lastround is not None) and (nowround != lastround + 1):
                logger.info('round jump %d to %d', lastround, nowround)
            for bh in self.block_handlers:
                bh(self, b)
            bb = b.get('block')
            transactions = bb.get('txns', [])
            for txn in transactions:
                for th in self.txn_handlers:
                    th(self, b, txn)
            self.record_block_progress(nowround)
            lastround = nowround

    def record_block_progress(self, round_number):
        """Appends round number to file at progress_log_path
        truncates progress log every 100000 blocks
        """
        if self._progresslog_write_count > 100000:
            if self._progresslog is not None:
                self._progresslog.close()
                self._progresslog = None
            nextpath = self.progress_log_path + '_next_' + time.strftime('%Y%m%d_%H%M%S', time.gmtime())
            nextlog = open(nextpath, 'xt')
            nextlog.write('{}\n'.format(round_number))
            nextlog.flush()
            nextlog.close() # could probably leave this open and keep writing to it
            os.replace(nextpath, self.progress_log_path)
            self._progresslog_write_count = 0
            # new log at standard location will be opened next time
            return
        if self._progresslog is None:
            if self.progress_log_path is None:
                return
            self._progresslog = open(self.progress_log_path, 'at')
            self._progresslog_write_count = 0
        self._progresslog.write('{}\n'.format(round_number))
        self._progresslog.flush()
        self._progresslog_write_count += 1

    def recover_progress(self):
        "Read progress_log_path for last seen round number"
        if self.progress_log_path is None:
            return None
        try:
            with open(self.progress_log_path, 'rt') as fin:
                fin.seek(0, 2)
                endpos = fin.tell()
                fin.seek(max(0, endpos - 100))
                raw = fin.read()
                lines = raw.splitlines()
                return int(lines[-1])
        except Exception as e:
            logger.info('could not recover progress: %s', e)
        return None

    def close(self):
        if self._progresslog is not None:
            self._progresslog.close()
            self._progresslog = None
        self._algod = None

def blockround(b):
    return b['block'].get('rnd', 0)

# block_printer is an example block handler; it takes two args, the bot and the block
def block_printer(bot, b):
    txns = b['block'].get('txns')
    if txns:
        print(json.dumps(b, indent=2))
    else:
        bround = blockround(b)
        if bround % 10 == 0:
            print(bround)

# block_counter is an example block handler; it takes two args, the bot and the block
def block_counter(bot, b):
    bround = blockround(b)
    if bround % 10 == 0:
        print(bround)

def all_tx_printer(bot, b, tx):
    print(json.dumps(tx['txn'], indent=2))

# big_tx_printer is an example txn handler; it takes three args, the bot the block and the transaction
def big_tx_printer(bot, b, tx):
    txn = tx['txn']
    amount = txn.get('amt', 0)
    aamt = txn.get('aamt', 0)
    if (amount > 1000) or (aamt > 1):
        print(json.dumps(tx, indent=2, sort_keys=True))

def make_arg_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument('-d', '--algod', default=None, help='algod data dir')
    ap.add_argument('-a', '--addr', default=None, help='algod host:port address')
    ap.add_argument('-t', '--token', default=None, help='algod API access token')
    ap.add_argument('--header', dest='headers', nargs='*', help='"Name: value" HTTP header (repeatable)')
    ap.add_argument('--verbose', default=False, action='store_true')
    ap.add_argument('--progress-file', default=None, help='file to write progress to')
    return ap

def header_list_to_dict(hlist):
    if not hlist:
        return None
    p = re.compile(r':\s+')
    out = {}
    for x in hlist:
        a, b = p.split(x, 1)
        out[a] = b
    return out

def setup(args, block_handlers=None, txn_handlers=None):
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    algorand_data = args.algod or os.getenv('ALGORAND_DATA')
    if not algorand_data and not (args.token and args.addr):
        sys.stderr.write('must specify algod data dir by $ALGORAND_DATA or -d/--algod; OR --a/--addr and -t/--token\n')
        sys.exit(1)

    if block_handlers is None and txn_handlers is None:
        txn_handlers = [big_tx_printer]
        block_handlers = [block_counter]
    bot = Algobot(
        algorand_data,
        token=args.token,
        addr=args.addr,
        headers=header_list_to_dict(args.headers),
        block_handlers=block_handlers,
        txn_handlers=txn_handlers,
        progress_log_path=args.progress_file,
    )

    killcount = [0]
    def gogently(signum, stackframe):
        count = killcount[0] + 1
        if count == 1:
            sys.stderr.write('signal received. starting graceful shutdown\n')
            bot.go = False
            killcount[0] = count
            return
        sys.stderr.write('second signal received. bye\n')
        sys.exit(1)

    signal.signal(signal.SIGTERM, gogently)
    signal.signal(signal.SIGINT, gogently)
    return bot

def main(block_handlers=None, txn_handlers=None, arghook=None):
    ap = make_arg_parser()
    args = ap.parse_args()

    if arghook is not None:
        arghook(args)

    bot = setup(args, block_handlers, txn_handlers)
    bot.loop()
    return

if __name__ == '__main__':
    main()
