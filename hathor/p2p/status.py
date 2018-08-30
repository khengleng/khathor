
from twisted.web import resource

import json
import time


class StatusResource(resource.Resource):
    isLeaf = True

    def __init__(self, factory):
        self.factory = factory

    def render_GET(self, request):
        request.setHeader(b'content-type', b'application/json; charset=utf-8')

        connected_peers = []
        for conn in self.factory.connected_peers.values():
            remote = conn.transport.getPeer()
            connected_peers.append({
                'id': conn.peer.id,
                'address': '{}:{}'.format(remote.host, remote.port),
                'last_message': time.time() - conn.last_message,
            })

        known_peers = []
        for peer in self.factory.peer_storage.values():
            known_peers.append({
                'id': peer.id,
                'entrypoints': peer.entrypoints,
            })

        data = {
            'server': {
                'uptime': time.time() - self.factory.start_time,
                'id': self.factory.my_peer.id,
                'entrypoints': self.factory.my_peer.entrypoints,
            },
            'known_peers': known_peers,
            'connected_peers': connected_peers,
        }
        return json.dumps(data, indent=4).encode('utf-8')
