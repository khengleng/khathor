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

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import rocksdb
    import structlog


class RocksDBIndexUtils:
    _db: 'rocksdb.DB'
    log: 'structlog.stdlib.BoundLogger'

    def __init__(self, db: 'rocksdb.DB') -> None:
        self._db = db

    def _fresh_cf(self, cf_name: bytes) -> 'rocksdb.ColumnFamilyHandle':
        """Ensure we have a working and fresh column family"""
        import rocksdb

        log_cf = self.log.new(cf=cf_name.decode('ascii'))
        _cf = self._db.get_column_family(cf_name)
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
        _cf = self._db.create_column_family(cf_name, rocksdb.ColumnFamilyOptions())
        new_id = _cf.id
        assert _cf is not None
        assert _cf.is_valid
        assert new_id != old_id
        log_cf.debug('got column family', is_valid=_cf.is_valid, id=_cf.id, old_id=old_id)
        return _cf
