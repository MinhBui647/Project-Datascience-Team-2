import unittest
from datetime import datetime, timezone
from scripts.access import raw_data_access as access

REV = '7' * 40

class FakeCursor:
    def __init__(self, *, ro='on', snapshot_state='completed', observations=None, record_rows=None, file_rows=None):
        self.ro = ro
        self.snapshot_state = snapshot_state
        self.observations = observations if observations is not None else [(19,)]
        self.record_rows = record_rows if record_rows is not None else [
            (101, None, 'line:1', 'csv_row', {'a': 1}, 'EPL', '2026_2027',
             datetime(2026, 10, 24, tzinfo=timezone.utc), {'test': True}, None),
            (102, None, 'line:2', 'csv_row', {'a': 2}, 'EPL', '2026_2027', None, {}, None)
        ]
        self.file_rows = file_rows if file_rows is not None else [
            ('2026_2027_new/matches/1.json', 19, 'json', 2),
            ('2026_2027_new/matches/2.json', 20, 'json', 3)
        ]
        self.sql = []
        self.args = []
        self.last = ''
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, query, args=None):
        q = ' '.join(query.lower().split())
        self.sql.append(q)
        self.args.append(args)
        self.last = q
        if not q.startswith('select '):
            raise AssertionError('Only SELECT may run')
    def fetchone(self):
        if 'current_database()' in self.last:
            return ('epl_raw_storage_v2', self.ro)
        if 'from raw.snapshot_runs' in self.last:
            return (367, self.snapshot_state, REV, 314, 296)
        raise AssertionError(self.last)
    def fetchall(self):
        if 'join raw.raw_files' in self.last:
            return self.file_rows
        if 'from raw.file_observations' in self.last:
            return self.observations
        if 'from raw.raw_records' in self.last:
            return self.record_rows
        raise AssertionError(self.last)

class FakeConn:
    def __init__(self, cur): self.cur = cur
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def cursor(self): return self.cur

class AccessTests(unittest.TestCase):
    def fixture(self, **kwargs):
        cursor = FakeCursor(**kwargs)
        return cursor, lambda: FakeConn(cursor)

    def test_files_listing(self):
        cur, connector = self.fixture()
        result = access.run(snapshot_id=367, mode='files', limit=1, connector=connector)
        self.assertEqual(result['returned_files'], 1)
        self.assertEqual(result['next_after_path'], '2026_2027_new/matches/1.json')
        self.assertFalse(result['write_executed'])
        self.assertEqual(cur.args[-1], (367, '', 2))

    def test_files_last_page(self):
        _, connector = self.fixture(file_rows=[('a.csv', 19, 'csv', 12)])
        r = access.run(snapshot_id=367, mode='files', limit=5, connector=connector)
        self.assertIsNone(r['next_after_path'])

    def test_records_page(self):
        cur, connector = self.fixture()
        r = access.run(snapshot_id=367, mode='records', path='2026_2027_new/matches/1.json', limit=1, connector=connector)
        self.assertEqual(r['records'][0]['raw_data'], {'a': 1})
        self.assertEqual(r['next_after_record_id'], 101)
        self.assertEqual(cur.args[-1], (19, 0, 2))

    def test_records_cursor(self):
        cur, connector = self.fixture(record_rows=[(151, None, 'line:4', 'csv_row', {}, 'EPL', None, None, {}, None)])
        r = access.run(snapshot_id=367, mode='records', path='a.json', limit=1, after_record_id=150, connector=connector)
        self.assertEqual(r['returned_records'], 1)
        self.assertEqual(cur.args[-1], (19, 150, 2))

    def test_completed_required(self):
        _, connector = self.fixture(snapshot_state='in_progress')
        with self.assertRaises(access.AccessBlocked):
            access.run(snapshot_id=367, mode='files', limit=1, connector=connector)

    def test_readonly_required(self):
        cur, connector = self.fixture(ro='off')
        with self.assertRaises(access.AccessBlocked):
            access.run(snapshot_id=367, mode='files', limit=1, connector=connector)
        self.assertEqual(len(cur.sql), 1)

    def test_unknown_file_refused(self):
        _, connector = self.fixture(observations=[])
        with self.assertRaises(access.AccessBlocked):
            access.run(snapshot_id=367, mode='records', path='file.csv', limit=1, connector=connector)

    def test_duplicate_observation_refused(self):
        _, connector = self.fixture(observations=[(19,), (19,)])
        with self.assertRaises(access.AccessBlocked):
            access.run(snapshot_id=367, mode='records', path='file.csv', limit=1, connector=connector)

    def test_wrong_cursor_rows_block(self):
        row = (5, None, 'line:1', 'csv_row', {}, 'EPL', None, None, {}, None)
        _, connector = self.fixture(record_rows=[row, row])
        with self.assertRaises(access.AccessBlocked):
            access.run(snapshot_id=367, mode='records', path='file.csv', limit=2, connector=connector)

    def test_invalid_file_path_no_connection(self):
        def reject(): raise AssertionError('Unexpected connection')
        for path in ('../x.json', '/etc/passwd', 'x\\y', 'a//b', 'a/./b', ''):
            with self.subTest(path=path), self.assertRaises(access.AccessBlocked):
                access.run(snapshot_id=367, mode='records', path=path, limit=1, connector=reject)

    def test_invalid_page_no_connection(self):
        def reject(): raise AssertionError('Unexpected connection')
        for limit in (0, 51, -1, True):
            with self.subTest(limit=limit), self.assertRaises(access.AccessBlocked):
                access.run(snapshot_id=367, mode='files', limit=limit, connector=reject)

    def test_invalid_snapshot_no_connection(self):
        def reject(): raise AssertionError('Unexpected connection')
        with self.assertRaises(access.AccessBlocked):
            access.run(snapshot_id=0, mode='files', limit=5, connector=reject)

    def test_invalid_record_cursor_no_connection(self):
        def reject(): raise AssertionError('Unexpected connection')
        with self.assertRaises(access.AccessBlocked):
            access.run(snapshot_id=367, mode='records', path='a.csv', after_record_id=-1, limit=5, connector=reject)

    def test_json_serialization_dates(self):
        cur, conn = self.fixture()
        result = access.run(snapshot_id=367, mode='records', path='abc.csv', limit=2, connector=conn)
        text = access.encode_bounded(result)
        self.assertIn('2026-10-24T00:00:00+00:00', text)

    def test_only_select_no_writes(self):
        cur, conn = self.fixture()
        access.run(snapshot_id=367, mode='files', limit=2, connector=conn)
        access.run(snapshot_id=367, mode='records', path='f.csv', limit=2, connector=conn)
        self.assertTrue(all(q.startswith('select ') for q in cur.sql))
        self.assertTrue(all('%s' in q or 'current_database' in q for q in cur.sql))

    def test_oversize_page_refused(self):
        original = access.MAX_OUTPUT_BYTES
        try:
            access.MAX_OUTPUT_BYTES = 50
            with self.assertRaises(access.AccessBlocked):
                access.encode_bounded({'raw_data': 'a' * 200})
        finally:
            access.MAX_OUTPUT_BYTES = original

    def test_bad_db_name(self):
        cur, conn = self.fixture()
        cur.fetchone = lambda: ('wrong_db', 'on')
        with self.assertRaises(access.AccessBlocked):
            access.run(snapshot_id=367, mode='files', limit=1, connector=conn)

if __name__ == '__main__': unittest.main()
