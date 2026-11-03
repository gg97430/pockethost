import datetime
import unittest
from unittest.mock import patch
import database_memory_reservations as m
G=1024**3
DB='a'*15

class Policy(unittest.TestCase):
    def test_explicit_reservation_and_unused_accounting(self):
        policy=m.parse_policy({'version':1,'databases':{DB:32768}})
        self.assertEqual(policy,{DB:32*G})
        self.assertEqual(m.unconsumed(policy,{DB:4*G}),28*G)
        self.assertEqual(m.unconsumed(policy,{DB:40*G}),0)
        self.assertEqual(m.unconsumed(policy,{}),32*G)
    def test_bad_policy_refused(self):
        for raw in [None,{},[],{'version':True,'databases':{}},{'version':1,'databases':[]},
                    {'version':1,'databases':{DB:True}},{'version':1,'databases':{DB:0}},
                    {'version':1,'databases':{DB:524289}},{'version':1,'databases':{'bad':32768}}]:
            with self.subTest(raw=raw),self.assertRaises(ValueError):m.parse_policy(raw)
    def test_fresh_summary_does_not_double_count_used_ram(self):
        policy={DB:32*G};now=1000
        summary={'version':1,'checkedAt':datetime.datetime.fromtimestamp(now,datetime.timezone.utc).isoformat(),
                 'policyHash':m.policy_hash(policy),'unconsumedBytes':28*G}
        with patch.object(m,'load_policy',return_value=policy),patch.object(m,'trusted_json',return_value=summary):
            self.assertEqual(m.reserved_headroom(now),28*G)
    def test_missing_stale_tampered_summary_keeps_full_reservation(self):
        policy={DB:32*G};base={'version':1,'checkedAt':'1970-01-01T00:16:40+00:00','policyHash':m.policy_hash(policy),'unconsumedBytes':28*G}
        for change in [{'checkedAt':'1970-01-01T00:00:00+00:00'},{'policyHash':'wrong'},
                       {'unconsumedBytes':-1},{'unconsumedBytes':33*G},{'unconsumedBytes':True}]:
            with self.subTest(change=change),patch.object(m,'load_policy',return_value=policy),patch.object(m,'trusted_json',return_value={**base,**change}):
                self.assertEqual(m.reserved_headroom(1000),32*G)
        with patch.object(m,'load_policy',return_value=policy),patch.object(m,'trusted_json',side_effect=FileNotFoundError):
            self.assertEqual(m.reserved_headroom(1000),32*G)
    def test_no_commitments_keeps_existing_behavior(self):
        with patch.object(m,'load_policy',return_value={}):self.assertEqual(m.reserved_headroom(),0)
if __name__=='__main__':unittest.main()
