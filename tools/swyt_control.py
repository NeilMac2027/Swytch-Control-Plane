#!/usr/bin/env python3
"""Small, fail-closed control-plane CLI. State is JSON, one record per lane."""
from __future__ import annotations
import argparse, datetime as dt, json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANES, LOCKS, HANDOFFS = ROOT/'lanes', ROOT/'locks', ROOT/'handoffs'
STATUSES = {'CLAIMED','ACTIVE','BLOCKED','HANDOFF_READY','REVIEW','READY_FOR_INTEGRATION','READY_FOR_CONVERGENCE','CLOSED','STALE_REQUIRES_RECONCILIATION'}
LOCK_NAMES = {'canonical-integration','migration-allocation','deployment','live-atz-write'}

def now(): return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
def parse_time(v): return dt.datetime.fromisoformat(v.replace('Z','+00:00'))
def expired(v): return parse_time(v) <= dt.datetime.now(dt.timezone.utc)
def norm(p): return os.path.normcase(os.path.normpath(str(p).replace('\\','/'))).replace('/','\\').rstrip('\\')
def path_overlap(a,b):
    a,b=norm(a),norm(b)
    return a==b or a.startswith(b+'\\') or b.startswith(a+'\\')
def load(path): return json.loads(path.read_text(encoding='utf-8'))
def dump(path, obj): path.write_text(json.dumps(obj, indent=2, sort_keys=True)+'\n', encoding='utf-8')
def lane_files(): return sorted(LANES.glob('*.json'))
def lanes(): return [load(p) for p in lane_files()]
def find_lane(i):
    p=LANES/(i+'.json')
    if not p.exists(): raise RuntimeError(f'lane not found: {i}')
    return p,load(p)
def machine(m):
    p=ROOT/'machines'/(m+'.json')
    if not p.exists(): raise RuntimeError(f'unknown machine: {m}')
    return load(p)
def fail(msg): raise RuntimeError(msg)
def check_claim(args, candidate, existing=None):
    if candidate['status'] not in STATUSES: fail('invalid status')
    m=machine(candidate['owner_machine'])
    if m.get('onedrive_policy') == 'ARCHIVE_RECOVERY_ONLY' and norm(candidate['worktree']).lower().startswith('c:\\onedrive'):
        fail('archive-only machine/worktree')
    if candidate['owner_machine']=='SNAPDRAGON' and candidate.get('product_scope',False) and not m['active_worker_clone_ready']:
        fail('SNAPDRAGON active worker clone is not ready for product work')
    for old in (existing or lanes()):
        if old.get('lane_id') == candidate.get('lane_id'): fail('duplicate lane')
        if old.get('branch') == candidate.get('branch'): fail('duplicate branch')
        if norm(old.get('worktree','')) == norm(candidate.get('worktree','')): fail('duplicate worktree')
        old_stale = old.get('status') == 'STALE_REQUIRES_RECONCILIATION' or expired(old.get('lease_until','9999-12-31T00:00:00+00:00'))
        if old_stale:
            if old.get('branch')==candidate.get('branch') or any(path_overlap(a,b) for a in old.get('owned_paths',[]) for b in candidate.get('owned_paths',[])): fail('stale conflicting claim requires reconciliation')
        if any(path_overlap(a,b) for a in old.get('owned_paths',[]) for b in candidate.get('owned_paths',[])): fail('owned path collision')
        if set(old.get('migration_numbers',[])) & set(candidate.get('migration_numbers',[])): fail('migration collision')
    for p in ROOT.glob('locks/*.json'):
        lock=load(p)
        if not expired(lock['lease_until']):
            if set(candidate.get('required_locks',[])) & {lock['lock_id']}: fail('exclusive lock held')
    return True
def git(*args): return subprocess.run(['git','-C',str(ROOT),*args],text=True,capture_output=True)
def mutate(action, paths):
    for _ in range(3):
        r=git('pull','--ff-only')
        if r.returncode: fail(r.stderr.strip() or 'git pull failed')
        action()
        git('add', *(str(p.relative_to(ROOT)) for p in paths))
        c=git('commit','-m','control-plane: establish shared peer-worker coordination')
        if c.returncode and 'nothing to commit' not in c.stdout+c.stderr: fail(c.stderr.strip())
        p=git('push')
        if p.returncode==0: return
        git('reset','--soft','HEAD~1')
        git('restore','--staged','.')
    fail('push race retries exhausted; state left unmerged')
def cmd_status(_):
    print(json.dumps({'lanes':lanes(),'locks':[load(p) for p in LOCKS.glob('*.json')],'handoffs':[load(p) for p in HANDOFFS.glob('*.json')]},indent=2))
def cmd_check(_):
    required={'schema_version','lane_id','description','owner_machine','repository','branch','worktree','base_sha','current_sha','status','owned_paths','migration_numbers','dependencies','handoff_target','claimed_at','heartbeat_at','lease_until','review_status','notes'}
    for l in lanes():
        if not required <= set(l): fail(f'missing lane fields: {l.get("lane_id")}')
        if l['status'] not in STATUSES: fail(f'invalid status: {l["lane_id"]}')
    print('PASS')
def cmd_claim(a):
    t=now(); c={'schema_version':1,'lane_id':a.lane_id,'description':a.description,'owner_machine':a.machine,'repository':a.repository,'branch':a.branch,'worktree':a.worktree,'base_sha':a.base_sha,'current_sha':a.current_sha,'status':'CLAIMED','owned_paths':a.path,'migration_numbers':a.migration,'dependencies':[],'handoff_target':None,'claimed_at':t,'heartbeat_at':t,'lease_until':(parse_time(t)+dt.timedelta(minutes=60)).isoformat(),'review_status':'PENDING','notes':a.notes,'product_scope':a.product_scope,'required_locks':a.required_lock}
    check_claim(a,c); p=LANES/(a.lane_id+'.json'); mutate(lambda: dump(p,c),[p]); print('CLAIMED',a.lane_id)
def cmd_heartbeat(a):
    p,l=find_lane(a.lane_id)
    if l['owner_machine']!=a.machine: fail('wrong owner')
    t=now(); l['heartbeat_at']=t; l['lease_until']=(parse_time(t)+dt.timedelta(minutes=60)).isoformat(); mutate(lambda:dump(p,l),[p]); print('HEARTBEAT',a.lane_id)
def cmd_release(a):
    p,l=find_lane(a.lane_id)
    if l['owner_machine']!=a.machine: fail('wrong owner')
    l['status']='CLOSED'; mutate(lambda:dump(p,l),[p]); print('RELEASED',a.lane_id)
def cmd_complete(a): cmd_release(a)
def cmd_handoff(a):
    p,l=find_lane(a.lane_id)
    if l['owner_machine']!=a.machine: fail('wrong owner')
    l['status']='HANDOFF_READY'; l['handoff_target']=a.target; h=HANDOFFS/(a.lane_id+'.json'); rec={'schema_version':1,'lane_id':a.lane_id,'source_machine':a.machine,'target_machine':a.target,'status':'PENDING','created_at':now()}; mutate(lambda:(dump(p,l),dump(h,rec)),[p,h]); print('HANDOFF_READY',a.lane_id)
def cmd_accept(a):
    p,l=find_lane(a.lane_id); h=HANDOFFS/(a.lane_id+'.json')
    if not h.exists() or load(h)['target_machine']!=a.machine: fail('handoff not addressed to machine')
    l['owner_machine']=a.machine; l['status']='ACTIVE'; rec=load(h); rec['status']='ACCEPTED'; rec['accepted_at']=now(); mutate(lambda:(dump(p,l),dump(h,rec)),[p,h]); print('ACCEPTED',a.lane_id)
def cmd_lock(a):
    if a.lock_id not in LOCK_NAMES: fail('invalid lock')
    p=LOCKS/(a.lock_id+'.json');
    if p.exists(): fail('lock held or stale; explicit unlock/reconciliation required')
    t=now(); rec={'schema_version':1,'lock_id':a.lock_id,'owner_machine':a.machine,'lane_id':a.lane_id,'claimed_at':t,'lease_until':(parse_time(t)+dt.timedelta(minutes=60)).isoformat()}; mutate(lambda:dump(p,rec),[p]); print('LOCKED',a.lock_id)
def cmd_unlock(a):
    p=LOCKS/(a.lock_id+'.json');
    if not p.exists() or load(p)['owner_machine']!=a.machine: fail('wrong owner or lock absent')
    rec=load(p); mutate(lambda:p.unlink(),[p]); print('UNLOCKED',rec['lock_id'])
def cmd_reconcile(a):
    p,l=find_lane(a.lane_id)
    if l['status']!='STALE_REQUIRES_RECONCILIATION': fail('lane is not stale')
    if l['owner_machine']!=a.machine: fail('wrong owner')
    l['status']='ACTIVE'; l['heartbeat_at']=now(); l['lease_until']=(parse_time(l['heartbeat_at'])+dt.timedelta(minutes=60)).isoformat(); mutate(lambda:dump(p,l),[p]); print('RECONCILED',a.lane_id)
def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    sub.add_parser('status').set_defaults(fn=cmd_status); sub.add_parser('check').set_defaults(fn=cmd_check)
    def common(p):
        p.add_argument('lane_id'); p.add_argument('--machine',required=True); p.add_argument('--branch',default=''); p.add_argument('--worktree',default=''); p.add_argument('--path',action='append',default=[]); p.add_argument('--migration',action='append',default=[]); p.add_argument('--repository',default='Swytch-AI'); p.add_argument('--base-sha',default=''); p.add_argument('--current-sha',default=''); p.add_argument('--description',default=''); p.add_argument('--notes',default=''); p.add_argument('--product-scope',action='store_true'); p.add_argument('--required-lock',action='append',default=[])
    p=sub.add_parser('claim'); common(p); p.set_defaults(fn=cmd_claim)
    for name,fn in [('heartbeat',cmd_heartbeat),('release',cmd_release),('complete',cmd_complete),('reconcile',cmd_reconcile)]: p=sub.add_parser(name); p.add_argument('lane_id'); p.add_argument('--machine',required=True); p.set_defaults(fn=fn)
    p=sub.add_parser('handoff'); p.add_argument('lane_id'); p.add_argument('--machine',required=True); p.add_argument('--target',required=True); p.set_defaults(fn=cmd_handoff)
    p=sub.add_parser('accept-handoff'); p.add_argument('lane_id'); p.add_argument('--machine',required=True); p.set_defaults(fn=cmd_accept)
    p=sub.add_parser('lock'); p.add_argument('lock_id'); p.add_argument('--machine',required=True); p.add_argument('--lane-id',default=''); p.set_defaults(fn=cmd_lock)
    p=sub.add_parser('unlock'); p.add_argument('lock_id'); p.add_argument('--machine',required=True); p.set_defaults(fn=cmd_unlock)
    try: ap.parse_args().fn(ap.parse_args())
    except RuntimeError as e: print('BLOCKED:',e,file=sys.stderr); return 2
if __name__=='__main__': main()
