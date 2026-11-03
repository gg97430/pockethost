#!/usr/bin/env python3
"""Narrow root operations invoked only by the authenticated Kolab controller.

No shell, tenant commands, environment reads or container lifecycle operations.
"""
import fcntl,hashlib,json,os,pathlib,re,secrets,sqlite3,stat,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from contextlib import closing,contextmanager
from database_memory_reservations import POLICY,MIB,load_policy,unconsumed
from database_memory_management import PATH as MANAGEMENT,load as load_management,policy_lock
from platform_maintenance import status as maintenance_status,acquire,release
from platform_memory_allocator import Engine,atomic,host_memory,read_json,database_inventory,ownership,scope,counters,round_up

CONFIG=pathlib.Path('/etc/kolab/memory-allocation.json')
STATE=pathlib.Path('/var/lib/kolab-memory-allocation/status.json')


class Conflict(ValueError):pass


@contextmanager
def controller_sql(database):
    """Read the controller as its owner; never create root-owned SQLite WAL files."""
    info=pathlib.Path(database).lstat();uid=os.geteuid()
    if not stat.S_ISREG(info.st_mode):raise ValueError('Invalid controller database')
    if uid!=0:
        if info.st_uid!=uid:raise PermissionError('Controller identity required')
        yield;return
    if info.st_uid!=1000:raise ValueError('Unexpected controller database owner')
    group,groups=os.getegid(),os.getgroups()
    try:
        os.setgroups([]);os.setegid(1000);os.seteuid(1000)
        yield
    finally:
        os.seteuid(0);os.setegid(group);os.setgroups(groups)


def revision(policy,management):
    return hashlib.sha256(json.dumps([policy,management],sort_keys=True,separators=(',',':')).encode()).hexdigest()


def status():
    policy,management=load_policy(),load_management()
    config=read_json(CONFIG,trusted=True)
    report=read_json(STATE,trusted=True)
    total,available=host_memory()
    with controller_sql(config['database']):
        with closing(sqlite3.connect(pathlib.Path(config['database']).as_uri()+'?mode=ro',uri=True,timeout=2))as db:
            db.execute('PRAGMA query_only=ON')
            names={row[0]:row[1]for row in db.execute('SELECT id,subdomain FROM instances')}
    measures={row['owner']:row for row in report.get('resources',[])if row.get('kind')=='databases'}
    return {'revision':revision(policy,management),'checkedAt':report['checkedAt'],'totalBytes':total,'availableBytes':available,'adaptiveMaximumMiB':min(16384,config['databaseMaximumMiB'],total//(2*MIB)),
        'reservedBytes':sum(policy.values()),'maintenance':maintenance_status(),
        'databases':[{'id':ident,'name':name,'mode':'fixed'if ident in policy else'adaptive',
            'reservationMiB':policy.get(ident,0)//MIB,'maximumMiB':management.get(ident,{}).get('maximumMiB',config['databaseMaximumMiB']),
            'usageBytes':measures.get(ident,{}).get('memoryBytes'),'limitBytes':measures.get(ident,{}).get('allocatedBytes'),
            'decision':measures.get(ident,{}).get('decision','unavailable')}
            for ident,name in names.items()]}


def validate_memory(value):
    if (not isinstance(value,dict)or set(value)!={'id','mode','maximumMiB','revision'}or
        not isinstance(value['id'],str)or not re.fullmatch(r'[a-z0-9]{15}',value['id'])or
        value['mode']not in ('adaptive','fixed')or type(value['maximumMiB'])is not int or not 512<=value['maximumMiB']<=524288 or
        not isinstance(value['revision'],str)or not re.fullmatch(r'[a-f0-9]{64}',value['revision'])):
        raise ValueError('Base, mode, plafond ou révision invalide.')


def assert_memory_editable(database,ident):
    # Additive migrations must not break older hosts. Locked imported copies
    # keep their reviewed RAM policy until the migration is released/removed.
    with controller_sql(database):
        with closing(sqlite3.connect(pathlib.Path(database).as_uri()+'?mode=ro',uri=True,timeout=2))as db:
            db.execute('PRAGMA query_only=ON')
            if 'migrationLock'not in {row[1]for row in db.execute('PRAGMA table_info(instances)')}:return
            row=db.execute('SELECT migrationLock FROM instances WHERE id=?',(ident,)).fetchone()
            if row and row[0]:raise Conflict('Cette copie appartient à une migration suspendue. Finalisez ou annulez la migration avant de modifier sa RAM.')


def set_memory(value):
    validate_memory(value)
    if os.geteuid()!=0:raise PermissionError('Root operator required')
    # A separate short-lived lock serializes admin edits; the allocator keeps its
    # own daemon lock and observes only complete atomic policy documents.
    with policy_lock():
        policy,management=load_policy(),load_management()
        if value['revision']!=revision(policy,management):raise Conflict('Ces réglages ont changé. Rechargez avant d’enregistrer.')
        config=read_json(CONFIG,trusted=True)
        with controller_sql(config['database']):ids,profiles,apps,busy=database_inventory(config['database'])
        ident=value['id'];amount=value['maximumMiB']*MIB
        if ident not in ids:raise ValueError('Cette base n’existe pas sur ce serveur.')
        if ident in busy:raise ValueError('Attendez la fin de la sauvegarde ou de la restauration.')
        assert_memory_editable(config['database'],ident)
        total,available=host_memory();reserve=max(2*1024**3,total*15//100)
        if value['mode']=='adaptive'and value['maximumMiB']!=min(16384,config['databaseMaximumMiB'],total//(2*MIB)):
            raise ValueError('Utilisez le plafond automatique commun du serveur. Le mode fixe permet une réservation différente.')
        if amount>total-reserve:raise ValueError('Le plafond dépasse la capacité disponible du serveur.')
        proposed={**policy}
        if value['mode']=='fixed':proposed[ident]=amount
        else:proposed.pop(ident,None)
        if sum(proposed.values())>total-reserve:raise ValueError('Les réservations dépassent la capacité du serveur.')
        engine=Engine('/var/run/docker.sock','databases')
        items=[];usage={}
        for row in engine.request('GET','/containers/json?all=0'):
            if not any(n.lstrip('/')in ids for n in row.get('Names',[])):continue
            item=engine.inspect(row['Id'])
            owner=ownership(item,'databases',ids,profiles,apps,config['instancesRoot'])
            if not owner:continue
            raw,working=counters(scope(item['pid'],item['cid']))
            usage[owner]=working
            if owner==ident:items.append((item,raw))
        if len(items)>1:raise ValueError('Plusieurs processus correspondent à cette base ; aucune modification.')
        if unconsumed(proposed,usage)-unconsumed(policy,usage)>max(0,available-reserve):
            raise ValueError('La RAM actuellement libre ne permet pas cette réservation.')
        target=None;previous=None
        if items:
            item,raw=items[0];previous=item['host']
            target=amount if value['mode']=='fixed'else min(amount,max(4096*MIB,previous['Memory'],round_up(raw*1.25+256*MIB)))
            if target<raw+256*MIB:raise ValueError('La base utilise trop de RAM pour ce plafond ; aucun processus arrêté.')
            fresh=engine.inspect(item['cid'])
            live,_=counters(scope(fresh['pid'],fresh['cid']))
            if fresh!=item or target<live+256*MIB:raise Conflict('La base a changé pendant le contrôle. Réessayez.')
            # Docker update treats zero as "leave unchanged" for this field.
            # A minimal 6 MiB kernel hint releases the Kolab capacity commitment
            # without recreating the container; its next start uses zero again.
            reservation=amount if value['mode']=='fixed'else (6*MIB if previous['MemoryReservation']else 0)
            # Reserve capacity first. Failure restores only operator policy, never business data.
            atomic(POLICY,{'version':1,'databases':{k:v//MIB for k,v in proposed.items()}},0o644)
            try:
                result=engine.request('POST','/containers/'+item['cid']+'/update',{'Memory':target,'MemoryReservation':reservation,'MemorySwap':-1})
                actual=engine.inspect(item['cid'])
                if result.get('Warnings')or actual['pid']!=item['pid']or not actual['running']or actual['host']['Memory']!=target or actual['host']['MemoryReservation']!=reservation:
                    raise ValueError('Le moteur n’a pas confirmé le nouveau plafond.')
            except Exception:
                atomic(POLICY,{'version':1,'databases':{k:v//MIB for k,v in policy.items()}},0o644)
                raise
        else:atomic(POLICY,{'version':1,'databases':{k:v//MIB for k,v in proposed.items()}},0o644)
        management[ident]={'mode':value['mode'],'maximumMiB':value['maximumMiB'],'revision':secrets.token_hex(16)}
        atomic(MANAGEMENT,{'version':1,'databases':management},0o644)
        return {'revision':revision(proposed,management),'appliedBytes':target,'restartRequired':False}


def operate(action,value):
    if os.geteuid()!=0:raise PermissionError('Root operator required')
    if action=='status'and value=={}:result=status()
    elif action=='memory':result=set_memory(value)
    elif action=='pause'and isinstance(value,dict)and set(value)=={'owner','seconds'}:
        if not isinstance(value['owner'],str)or not re.fullmatch(r'admin-[a-z0-9-]{5,60}',value['owner'])or type(value['seconds'])is not int or not 30<=value['seconds']<=1800:raise ValueError('Invalid maintenance request')
        result=acquire(value['owner'],value['seconds'])
    elif action=='resume'and isinstance(value,dict)and set(value)=={'owner'}:
        if not isinstance(value['owner'],str)or not re.fullmatch(r'admin-[a-z0-9-]{5,60}',value['owner']):raise ValueError('Invalid maintenance request')
        result=release(value['owner'])
    else:raise ValueError('Unknown operator action')
    return result


def response(action,value):
    try:return {'ok':True,'value':operate(action,value)}
    except Conflict as error:return {'ok':False,'status':409,'message':str(error)}
    except (ValueError,PermissionError)as error:return {'ok':False,'status':400,'message':str(error)}
    except Exception:return {'ok':False,'status':503,'message':'Le contrôle local est indisponible ; aucun redémarrage demandé.'}


def main():
    if os.geteuid()!=0:raise PermissionError('Root operator required')
    if len(sys.argv)!=3 or len(sys.argv[2])>8192:raise ValueError('Invalid operator request')
    print(json.dumps(response(sys.argv[1],json.loads(sys.argv[2]))))


if __name__=='__main__':
    try:main()
    except Conflict as error:print(json.dumps({'ok':False,'status':409,'message':str(error)}))
    except (ValueError,PermissionError)as error:print(json.dumps({'ok':False,'status':400,'message':str(error)}))
    except Exception:print(json.dumps({'ok':False,'status':503,'message':'Le contrôle local est indisponible ; aucun redémarrage demandé.'}))
