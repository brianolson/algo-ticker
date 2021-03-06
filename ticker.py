#!/usr/bin/python3
#
# Usage:
#  python3 ticker.py --algod "${ALGORAND_DATA}"
#
# pip install py-algorand-sdk websockets

import asyncio
import http
import json
import logging
import os
import threading

import websockets

import algobot

bot = None

logger = logging.getLogger(__name__)
logging.getLogger('websockets').setLevel(logging.WARNING)

class CircularBuffer:
    def __init__(self, size=10000):
        self.lock = threading.Lock()
        self.size = size
        self.buf = [None] * size
        self.nextpos = 0
        self.posted = threading.Condition(self.lock)

    def get(self, pos):
        with self.lock:
            while pos >= self.nextpos:
                self.posted.wait()
            if (self.nextpos - pos) >= self.size:
                # too far behind
                pos = self.nextpos - self.size + 1
            return self.buf[pos % self.size], pos
    def put(self, ob):
        with self.lock:
            self.buf[self.nextpos % self.size] = ob
            self.nextpos += 1
            self.posted.notify_all()
    def pos(self):
        with self.lock:
            return self.nextpos - 1

cbuf = CircularBuffer()

def block_dbglog(bot, b):
    logger.debug('round %d, %d txns', b['block'].get('rnd', 0), len(b['block'].get('txns', [])))
    cbuf.put({'_round':b['block'].get('rnd',0)})

def tx_to_ws(bot, b, tx):
    #logger.debug('tx r=%d', b['block'].get('rnd', 0))
    tx['_round'] = b['block'].get('rnd', 0)
    cbuf.put(tx)

fabs = os.path.abspath(__file__)
fdir = os.path.dirname(fabs)

def _get_asset(path):
    if path.startswith('/v2'):
        path = path[3:]
    global bot
    return json.dumps(bot.algod().algod_request('GET', path)).encode()

async def get_asset(path):
    return await asyncio.get_event_loop().run_in_executor(None, _get_asset, path)

async def process_request(path, request_headers):
    if path == '/stream':
        return None
    if path.startswith('/v2/assets/'):
        data = await get_asset(path)
        logger.debug('%s %r %d', path, data[:20], len(data))
        return http.HTTPStatus.OK, [('Content-Type', 'application/json'), ('Content-Length', str(len(data)))], data
    fpath = os.path.join(fdir, path[1:])
    logger.debug('GET %r -> %r', path, fpath)
    if os.path.exists(fpath):
        response_headers = []
        if fpath.endswith('.html'):
            response_headers.append( ('Content-Type', 'text/html') )
        elif fpath.endswith('.js'):
            response_headers.append( ('Content-Type', 'text/javascript') )
        with open(fpath, 'rb') as fin:
            data = fin.read()
        return http.HTTPStatus.OK, response_headers, data
    return http.HTTPStatus.NOT_FOUND, [], b''

async def handler(websocket, path):
    pos = cbuf.pos() - 30
    while True:
        txn, pos = await asyncio.get_event_loop().run_in_executor(None, cbuf.get, pos)
        #logger.debug('send tx r=%d', txn['_round'])
        await websocket.send(json.dumps(txn))
        pos += 1

def main():
    global bot
    ap = algobot.make_arg_parser()
    args = ap.parse_args()
    bot = algobot.setup(args, [block_dbglog], [tx_to_ws])
    threading.Thread(target=bot.loop).start()

    print('http://localhost:8580/ticker.html')
    start_server = websockets.serve(handler, "", 8580, process_request=process_request)

    asyncio.get_event_loop().run_until_complete(start_server)
    asyncio.get_event_loop().run_forever()

if __name__ == '__main__':
    main()
