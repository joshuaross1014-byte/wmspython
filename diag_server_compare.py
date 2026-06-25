"""Server/instance-level comparison between PROD and CLOUD beyond the WMS_DB DB:
SQL Agent jobs, databases on the instance, linked servers, Service Broker,
server configuration, and server logins. Read-only."""
import time, wms_connect, cloud_connect, pandas as pd
from sqlalchemy import text

Q = {
 "databases": """
   SELECT name, state_desc, recovery_model_desc, compatibility_level, collation_name
   FROM sys.databases WITH (NOLOCK)
   WHERE database_id > 4 ORDER BY name;""",
 "agent_jobs": """
   SELECT j.name, CAST(j.enabled AS int) enabled,
          (SELECT COUNT(*) FROM msdb.dbo.sysjobsteps s WHERE s.job_id=j.job_id) steps
   FROM msdb.dbo.sysjobs j WITH (NOLOCK) ORDER BY j.name;""",
 "agent_schedules": """
   SELECT j.name AS job, sch.name AS schedule, CAST(sch.enabled AS int) enabled,
          sch.freq_type, sch.freq_interval, sch.freq_subday_type,
          sch.freq_subday_interval, sch.active_start_time
   FROM msdb.dbo.sysjobs j WITH (NOLOCK)
   JOIN msdb.dbo.sysjobschedules js WITH (NOLOCK) ON js.job_id=j.job_id
   JOIN msdb.dbo.sysschedules sch WITH (NOLOCK) ON sch.schedule_id=js.schedule_id
   ORDER BY j.name, sch.name;""",
 "linked_servers": """
   SELECT name, product, provider, data_source, is_linked
   FROM sys.servers WITH (NOLOCK) WHERE server_id<>0 ORDER BY name;""",
 "broker_queues": """
   SELECT name, CAST(is_activation_enabled AS int) act,
          CAST(is_receive_enabled AS int) rcv, CAST(is_enqueue_enabled AS int) enq
   FROM sys.service_queues WITH (NOLOCK) WHERE is_ms_shipped=0 ORDER BY name;""",
 "server_config": """
   SELECT name, CAST(value_in_use AS bigint) v
   FROM sys.configurations WITH (NOLOCK) ORDER BY name;""",
 "logins": """
   SELECT name, type_desc, CAST(is_disabled AS int) disabled
   FROM sys.server_principals WITH (NOLOCK)
   WHERE type IN ('S','U','G') AND name NOT LIKE '##%' ORDER BY name;""",
}

def conn(mod, tries=8):
    for i in range(tries):
        try: return mod.get_engine().connect()
        except Exception:
            if i==tries-1: raise
            time.sleep(2)

def grab(c):
    out={}
    for k,sql in Q.items():
        try: out[k]=pd.read_sql(text(sql), c)
        except Exception as e: out[k]=str(e)[:90]
    return out

with conn(wms_connect) as c: P=grab(c)
with conn(cloud_connect) as c: K=grab(c)

def show(title, key, idcols):
    print("="*90); print(title); print("="*90)
    p,k = P[key], K[key]
    if isinstance(p,str) or isinstance(k,str):
        print(f"  (query error) prod={p if isinstance(p,str) else 'ok'} | cloud={k if isinstance(k,str) else 'ok'}"); print(); return
    pk=set(map(tuple, p[idcols].values.tolist()))
    kk=set(map(tuple, k[idcols].values.tolist()))
    print(f"  PROD count={len(p)}   CLOUD count={len(k)}")
    op=sorted(pk-kk); ok=sorted(kk-pk)
    if op:
        print(f"  ONLY IN PROD [{len(op)}]:")
        for x in op[:40]: print("    -", x)
    if ok:
        print(f"  ONLY IN CLOUD [{len(ok)}]:")
        for x in ok[:40]: print("    +", x)
    if not op and not ok: print("  identity sets MATCH")
    print()

show("DATABASES ON INSTANCE","databases",["name"])
show("SQL AGENT JOBS","agent_jobs",["name","enabled","steps"])
show("SQL AGENT SCHEDULES","agent_schedules",["job","schedule","enabled","freq_type","freq_interval","freq_subday_type","freq_subday_interval","active_start_time"])
show("LINKED SERVERS","linked_servers",["name","product","data_source"])
show("SERVICE BROKER QUEUES (WMS_DB)","broker_queues",["name","act","rcv","enq"])
show("SERVER LOGINS","logins",["name","type_desc","disabled"])

# server_config: show value differences only
print("="*90); print("SERVER CONFIGURATION (sp_configure) differences"); print("="*90)
pc,kc = P["server_config"], K["server_config"]
if isinstance(pc,str) or isinstance(kc,str):
    print("  query error")
else:
    m=pc.merge(kc,on="name",suffixes=("_prod","_cloud"))
    diff=m[m["v_prod"]!=m["v_cloud"]]
    if len(diff)==0: print("  all server config values identical")
    else:
        for _,r in diff.iterrows():
            print(f"  {r['name']:<45} prod={r['v_prod']:<14} cloud={r['v_cloud']}")
