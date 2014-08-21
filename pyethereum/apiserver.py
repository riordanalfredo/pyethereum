import logging
import threading
import json

import bottle

from pyethereum.chainmanager import chain_manager
from pyethereum.peermanager import peer_manager
import pyethereum.dispatch as dispatch
from pyethereum.blocks import block_structure
import pyethereum.signals as signals
from pyethereum.transactions import Transaction

logger = logging.getLogger(__name__)
base_url = '/api/v0alpha'

app = bottle.Bottle()
app.config['autojson'] = False
app.install(bottle.JSONPlugin(json_dumps=lambda s: json.dumps(s, sort_keys=True)))


class ApiServer(threading.Thread):

    def __init__(self):
        super(ApiServer, self).__init__()
        self.daemon = True
        self.listen_host = '127.0.0.1'
        self.port = 30203

    def configure(self, config):
        self.listen_host = config.get('api', 'listen_host')
        self.port = config.getint('api', 'listen_port')

    def run(self):
        middleware = CorsMiddleware(app)
        bottle.run(middleware, server='waitress',
                   host=self.listen_host, port=self.port)

# ###### create server ######

api_server = ApiServer()


@dispatch.receiver(signals.config_ready)
def config_api_server(sender, config, **kwargs):
    api_server.configure(config)


# #######cors##############
class CorsMiddleware:
    HEADERS = [
        ('Access-Control-Allow-Origin', '*'),
        ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'),
        ('Access-Control-Allow-Headers',
         'Origin, Accept, Content-Type, X-Requested-With, X-CSRF-Token')
    ]

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ["REQUEST_METHOD"] == "OPTIONS":
            start_response('200 OK',
                           CorsMiddleware.HEADERS + [('Content-Length', "0")])
            return ""
        else:
            def my_start_response(status, headers, exc_info=None):
                headers.extend(CorsMiddleware.HEADERS)

                return start_response(status, headers, exc_info)
            return self.app(environ, my_start_response)


# ######### Utilities ########
def load_json_req():
    json_body = bottle.request.json
    if not json_body:
        json_body = json.load(bottle.request.body)
    return json_body


# ######## Blocks ############
def make_blocks_response(blocks):
    return dict(blocks = [block.to_dict() for block in blocks])


@app.get(base_url + '/blocks/')
def blocks():
    logger.debug('blocks/')
    return make_blocks_response(chain_manager.get_chain(start='', count=20))

@app.get(base_url + '/blocks/<arg>')
def block(arg=None):
    """
    /blocks/            return N last blocks
    /blocks/head        return head
    /blocks/<int>       return block by number
    /blocks/<hex>       return block by hexhash
    """
    logger.debug('blocks/%s', arg)
    try:
        if arg is None:
            return blocks()
        elif arg == 'head':
            block = chain_manager.head
        elif arg.isdigit():
            block = chain_manager.get(chain_manager.index.get_block_by_number(int(arg)))
        else:
            try:
                h = arg.decode('hex')
            except TypeError:
                raise KeyError
            block = chain_manager.get(h)
    except KeyError:
        return bottle.abort(404, 'No block  %s' % arg)
    return make_blocks_response([block])


# ######## Transactions ############
def make_transaction_response(txs):
    return dict(transactions = [tx.to_dict() for tx in txs])

@app.put(base_url + '/transactions/')
def add_transaction():
    # request.json FIXME / post json encoded data? i.e. the representation of
    # a tx
    hex_data = bottle.request.body.read()
    logger.debug('PUT transactions/ %s', hex_data)
    tx = Transaction.hex_deserialize(hex_data)
    signals.local_transaction_received.send(sender=None, transaction=tx)
    return bottle.redirect(base_url + '/transactions/' + tx.hex_hash())
    """

    HTTP status code 200 OK for a successful PUT of an update to an existing resource. No response body needed. (Per Section 9.6, 204 No Content is even more appropriate.)
    HTTP status code 201 Created for a successful PUT of a new resource, with URIs and metadata of the new resource echoed in the response body. (RFC 2616 Section 10.2.2)
    HTTP status code 409 Conflict for a PUT that is unsuccessful due to a 3rd-party modification, with a list of differences between the attempted update and the current resource in the response body. (RFC 2616 Section 10.4.10)
    HTTP status code 400 Bad Request for an unsuccessful PUT, with natural-language text (such as English) in the response body that explains why the PUT failed. (RFC 2616 Section 10.4)
    """


@app.get(base_url + '/transactions/<arg>')
def get_transactions(arg=None):
    """
    /transactions/<hex>          return transaction by hexhash
    """
    logger.debug('GET transactions/%s', arg)
    try:
        tx_hash = arg.decode('hex')
    except TypeError:
        bottle.abort(500, 'No hex  %s' % arg)
    try: # index
        tx, blk = chain_manager.index.get_transaction(tx_hash)
    except KeyError:
        # try miner
        txs = chain_manager.miner.get_transactions()
        found = [tx for tx in txs if tx.hex_hash() == arg]
        if not found:
            return bottle.abort(404, 'No Transaction  %s' % arg)
        tx, blk = found[0], chain_manager.miner.block
    # response
    tx = tx.to_dict()
    tx['block'] = blk.hex_hash()
    if not chain_manager.in_main_branch(blk):
        tx['confirmations'] = 0
    else:
        tx['confirmations'] = chain_manager.head.number - blk.number
    return dict(transactions=[tx])


# ######## Accounts ############
@app.get(base_url + '/accounts/')
def accounts():
    logger.debug('accounts')

@app.get(base_url + '/accounts/<address>')
def account(address=None):
    logger.debug('accounts/%s', address)
    data = chain_manager.head.account_to_dict(address)
    logger.debug(data)
    return data



# ######## Peers ###################
def make_peers_response(peers):
    objs = [dict(ip=ip, port=port, node_id=node_id.encode('hex'))
            for (ip, port, node_id) in peers]
    return dict(peers=objs)


@app.get(base_url + '/peers/connected')
def connected_peers():
    return make_peers_response(peer_manager.get_connected_peer_addresses())


@app.get(base_url + '/peers/known')
def known_peers():
    return make_peers_response(peer_manager.get_known_peer_addresses())
