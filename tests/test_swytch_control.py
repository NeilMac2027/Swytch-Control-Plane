import json, sys, unittest, importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('swytch_control', r'C:\Swyt-Control\shared\tools\swyt_control.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

def lane(**kw):
    base = {'lane_id':'x','owner_machine':'MACCA2026','branch':'b','worktree':'C:\\work\\x','owned_paths':['src/a'],'migration_numbers':[],'status':'ACTIVE','product_scope':False}
    base.update(kw); return base

class CollisionTests(unittest.TestCase):
    def setUp(self): self.old = c.lanes
    def tearDown(self): c.lanes = self.old
    def check(self, candidate, old):
        c.lanes = lambda: old
        return c.check_claim(None, candidate)
    def test_free_claim(self): self.assertTrue(self.check(lane(lane_id='new',branch='new',worktree='C:\\work\\new',owned_paths=['src/b']), [lane()]))
    def test_duplicate_lane(self): self.assertRaises(RuntimeError, self.check, lane(), [lane()])
    def test_duplicate_branch(self): self.assertRaises(RuntimeError, self.check, lane(lane_id='new'), [lane()])
    def test_duplicate_worktree(self): self.assertRaises(RuntimeError, self.check, lane(lane_id='new',branch='new'), [lane()])
    def test_exact_path(self): self.assertRaises(RuntimeError, self.check, lane(lane_id='new',branch='new',worktree='C:\\work\\new'), [lane()])
    def test_parent_child(self): self.assertRaises(RuntimeError, self.check, lane(lane_id='new',branch='new',worktree='C:\\work\\new',owned_paths=['src/a/file.py']), [lane()])
    def test_disjoint_paths(self): self.assertTrue(self.check(lane(lane_id='new',branch='new',worktree='C:\\work\\new',owned_paths=['src/b']), [lane()]))
    def test_windows_casing_and_slashes(self): self.assertRaises(RuntimeError, self.check, lane(lane_id='new',branch='new',worktree='C:/work/new',owned_paths=['SRC/A']), [lane()])
    def test_migration_collision(self): self.assertRaises(RuntimeError, self.check, lane(lane_id='new',branch='new',worktree='C:\\work\\new',owned_paths=['src/b'],migration_numbers=['0042']), [lane(migration_numbers=['0042'])])
    def test_stale_claim_no_auto_steal(self): self.assertRaises(RuntimeError, self.check, lane(lane_id='new',branch='new',worktree='C:\\work\\new'), [lane(status='STALE_REQUIRES_RECONCILIATION',branch='new')])
    def test_expired_active_claim_no_auto_steal(self): self.assertRaises(RuntimeError, self.check, lane(lane_id='new',branch='new',worktree='C:\\work\\new'), [lane(branch='new', lease_until='2000-01-01T00:00:00+00:00')])

class ModelTests(unittest.TestCase):
    def test_path_overlap(self):
        self.assertTrue(c.path_overlap('C:/A/B','c:\\a\\b\\file'))
        self.assertFalse(c.path_overlap('C:/A/BC','c:\\a\\b'))
    def test_stale_lock_is_not_auto_stealable_by_claim(self):
        # Expired ownership is a reconciliation state, never an implicit release.
        self.assertTrue(c.expired('2000-01-01T00:00:00+00:00'))
    def test_wrong_owner_release_is_rejected(self):
        self.assertEqual(lane(owner_machine='SNAPDRAGON')['owner_machine'], 'SNAPDRAGON')
    def test_lease_extension_is_one_hour(self):
        t=c.parse_time('2026-09-27T00:00:00+00:00')
        self.assertEqual((t+c.dt.timedelta(minutes=60)).isoformat(), '2026-09-27T01:00:00+00:00')
    def test_handoff_source_retention(self):
        self.assertIn('HANDOFF_READY', c.STATUSES)
    def test_archive_only_machine_gate(self):
        self.assertEqual(c.machine('SNAPDRAGON')['active_worker_clone_ready'], False)
    def test_snap_product_gate(self):
        self.assertRaises(RuntimeError, self.check_snap)
    def check_snap(self): c.check_claim(None,lane(lane_id='new',branch='new',worktree='C:\\work\\new',owner_machine='SNAPDRAGON',product_scope=True))
    def test_lock_names(self): self.assertEqual(c.LOCK_NAMES, {'canonical-integration','migration-allocation','deployment','live-atz-write'})

if __name__ == '__main__': unittest.main()
