#!/usr/bin/python3
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

def tx_to_ws(bot, b, tx):
    #logger.debug('tx r=%d', b['block'].get('rnd', 0))
    tx['_round'] = b['block'].get('rnd', 0)
    cbuf.put(tx)

fabs = os.path.abspath(__file__)
fdir = os.path.dirname(fabs)

async def process_request(path, request_headers):
    if path == '/stream':
        return None
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

ap = algobot.make_arg_parser()
args = ap.parse_args()
bot = algobot.setup(args, [block_dbglog], [tx_to_ws])
threading.Thread(target=bot.loop).start()

start_server = websockets.serve(handler, "", 8580, process_request=process_request)

asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()
