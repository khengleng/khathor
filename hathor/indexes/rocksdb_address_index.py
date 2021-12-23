# Copyright 2021 Hathor Labs
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import TYPE_CHECKING, Iterable, List, Optional, Set, Tuple

from structlog import get_logger

from hathor.indexes.address_index import AddressIndex
from hathor.pubsub import HathorEvents
from hathor.transaction import BaseTransaction
from hathor.transaction.scripts import parse_address_script

if TYPE_CHECKING:  # pragma: no cover
    import rocksdb

    from hathor.pubsub import EventArguments, PubSubManager
    from hathor.transaction import TxOutput

logger = get_logger()

_CF_NAME_ADDRESS_INDEX = b'address-index'


class RocksDBAddressIndex(AddressIndex):
    """ Index of inputs/outputs by address.

    This index uses rocksdb and the following key format:

        key = [address][tx.timestamp][tx.hash]
              |--34b--||--4 bytes---||--32b--|

    It works nicely because rocksdb uses a tree sorted by key under the hoods.

    The timestamp must be serialized in big-endian, so ts1 > ts2 implies that bytes(ts1) > bytes(ts2),
    hence the transactions are sorted by timestamp.
    """
    def __init__(self, db: 'rocksdb.DB', *, cf_name: Optional[bytes] = None,
                 pubsub: Optional['PubSubManager'] = None) -> None:
        self.log = logger.new()
        self._db = db

        # column family stuff
        self._cf_name = cf_name or _CF_NAME_ADDRESS_INDEX
        self._reset_cf()

        self.pubsub = pubsub
        if self.pubsub:
            self.subscribe_pubsub_events()

    def _reset_cf(self) -> None:
        """Ensure we have a working and fresh column family"""
        import rocksdb

        log_cf = self.log.new(cf=self._cf_name.decode('ascii'))
        _cf = self._db.get_column_family(self._cf_name)
        # XXX: dropping column because initialization currently expects a fresh index
        if _cf is not None:
            old_id = _cf.id
            log_cf.debug('drop existing column family')
            self._db.drop_column_family(_cf)
        else:
            old_id = None
            log_cf.debug('no need to drop column family')
        del _cf
        log_cf.debug('create fresh column family')
        _cf = self._db.create_column_family(self._cf_name, rocksdb.ColumnFamilyOptions())
        new_id = _cf.id
        assert _cf is not None
        assert _cf.is_valid
        assert new_id != old_id
        self._cf = _cf
        log_cf.debug('got column family', is_valid=_cf.is_valid, id=_cf.id, old_id=old_id)

    def _to_key(self, address: str, tx: Optional[BaseTransaction] = None) -> bytes:
        import struct
        assert len(address) == 34
        key = address.encode('ascii')
        if tx:
            assert tx.hash is not None
            assert len(tx.hash) == 32
            key += struct.pack('>I', tx.timestamp) + tx.hash
            assert len(key) == 34 + 4 + 32
        return key

    def _from_key(self, key: bytes) -> Tuple[str, int, bytes]:
        import struct
        assert len(key) == 34 + 4 + 32
        address = key[:34].decode('ascii')
        timestamp: int
        (timestamp,) = struct.unpack('>I', key[34:38])
        tx_hash = key[38:]
        assert len(address) == 34
        assert len(tx_hash) == 32
        return address, timestamp, tx_hash

    def subscribe_pubsub_events(self) -> None:
        """ Subscribe wallet index to receive voided/winner tx pubsub events
        """
        assert self.pubsub is not None
        # Subscribe to voided/winner events
        events = [HathorEvents.STORAGE_TX_VOIDED, HathorEvents.STORAGE_TX_WINNER]
        for event in events:
            self.pubsub.subscribe(event, self.handle_tx_event)

    def _get_addresses(self, tx: BaseTransaction) -> Set[str]:
        """ Return a set of addresses collected from tx's inputs and outputs.
        """
        assert tx.storage is not None
        addresses: Set[str] = set()

        def add_address_from_output(output: 'TxOutput') -> None:
            script_type_out = parse_address_script(output.script)
            if script_type_out:
                address = script_type_out.address
                addresses.add(address)

        for txin in tx.inputs:
            tx2 = tx.storage.get_transaction(txin.tx_id)
            txout = tx2.outputs[txin.index]
            add_address_from_output(txout)

        for txout in tx.outputs:
            add_address_from_output(txout)

        return addresses

    def publish_tx(self, tx: BaseTransaction, *, addresses: Optional[Iterable[str]] = None) -> None:
        """ Publish WALLET_ADDRESS_HISTORY for all addresses of a transaction.
        """
        if not self.pubsub:
            return
        if addresses is None:
            addresses = self._get_addresses(tx)
        data = tx.to_json_extended()
        for address in addresses:
            self.pubsub.publish(HathorEvents.WALLET_ADDRESS_HISTORY, address=address, history=data)

    def add_tx(self, tx: BaseTransaction) -> None:
        """ Add tx inputs and outputs to the wallet index (indexed by its addresses).
        """
        assert tx.hash is not None

        addresses = self._get_addresses(tx)
        for address in addresses:
            self.log.debug('put address', address=address)
            self._db.put((self._cf, self._to_key(address, tx)), b'')

        self.publish_tx(tx, addresses=addresses)

    def remove_tx(self, tx: BaseTransaction) -> None:
        """ Remove tx inputs and outputs from the wallet index (indexed by its addresses).
        """
        assert tx.hash is not None

        addresses = self._get_addresses(tx)
        for address in addresses:
            self.log.debug('delete address', address=address)
            self._db.delete((self._cf, self._to_key(address, tx)))

    def handle_tx_event(self, key: HathorEvents, args: 'EventArguments') -> None:
        """ This method is called when pubsub publishes an event that we subscribed
        """
        data = args.__dict__
        tx = data['tx']
        meta = tx.get_metadata()
        if meta.has_voided_by_changed_since_last_call() or meta.has_spent_by_changed_since_last_call():
            self.publish_tx(tx)

    def _get_from_address_iter(self, address: str) -> Iterable[bytes]:
        self.log.debug('seek to', address=address)
        it = self._db.iterkeys(self._cf)
        it.seek(self._to_key(address))
        for key in it:
            addr, _, tx_hash = self._from_key(key)
            if addr != address:
                break
            self.log.debug('seek found', tx=tx_hash.hex())
            yield tx_hash
        self.log.debug('seek end')

    def get_from_address(self, address: str) -> List[bytes]:
        """ Get list of transaction hashes of an address
        """
        return list(self._get_from_address_iter(address))

    def get_sorted_from_address(self, address: str) -> List[bytes]:
        """ Get a sorted list of transaction hashes of an address
        """
        return list(self._get_from_address_iter(address))

    def is_address_empty(self, address: str) -> bool:
        self.log.debug('seek to', address=address)
        it = self._db.iterkeys(self._cf)
        it.seek(self._to_key(address))
        key = it.get()
        addr, _, _ = self._from_key(key)
        is_empty = addr == address
        self.log.debug('seek empty', is_empty=is_empty)
        return is_empty
