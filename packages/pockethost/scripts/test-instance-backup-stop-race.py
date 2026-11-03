#!/usr/bin/env python3
"""Exercise the production stop loop against an in-flight daemon status save."""
import json,pathlib,re,subprocess,unittest
SOURCE=pathlib.Path(__file__).resolve().parents[1]/'src/mothership-app/src/lib/handlers/instance/api/HandleInstanceBackups.ts'
class StopRace(unittest.TestCase):
 def run_case(self, states, expected_writes, expected_sleeps, fails=False):
  source=SOURCE.read_text();match=re.search(r'const waitUntilIdle = \(instanceId: string\) => \{(.*?)\n\}',source,re.S);self.assertIsNotNone(match)
  script='''let i=0,writes=[],sleeps=0,failed=false;const states=STATES;const MAX_STOP_WAIT_SECONDS=states.length;const findInstance=id=>{if(id!=='target')throw Error('wrong id');const s=states[Math.min(i,states.length-1)];return{getBool:k=>s.power,getString:k=>s.status}};const setInstancePower=(id,value)=>writes.push([id,value]);const sleepOneSecond=()=>{sleeps++;i++};const waitUntilIdle=instanceId=>{BODY\n};try{waitUntilIdle('target')}catch{failed=true}process.stdout.write(JSON.stringify({writes,sleeps,failed}));'''.replace('STATES',json.dumps(states)).replace('BODY',match.group(1))
  actual=json.loads(subprocess.check_output(['node','-e',script],text=True));self.assertEqual(actual,{'writes':[['target',False]]*expected_writes,'sleeps':expected_sleeps,'failed':fails})
 def test_late_daemon_save_cannot_cancel_stop(self):self.run_case([{'power':True,'status':'running'},{'power':False,'status':'idle'}],1,1)
 def test_powered_on_idle_is_not_safe_to_restore(self):self.run_case([{'power':True,'status':'idle'},{'power':False,'status':'idle'}],1,1)
 def test_idle_without_power_needs_no_write(self):self.run_case([{'power':False,'status':'idle'}],0,0)
 def test_running_shutdown_waits_for_acknowledgement(self):self.run_case([{'power':False,'status':'running'},{'power':False,'status':'idle'}],0,1)
 def test_repeated_late_saves_remain_bounded(self):self.run_case([{'power':True,'status':'running'}]*3,3,3,True)
if __name__=='__main__':unittest.main()
