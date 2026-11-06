import { useState } from 'react';
import { api } from '../services/api';
import { absoluteDeadline } from '../services/data';
import { PageTitle, useLoad, Loading, ErrorNotice } from '../components/ui';
import TaskTable, {allTasks} from '../components/TaskTable';
import DependencyGraph from '../components/DependencyGraph';
export default function Tasks(){
  const data=useLoad(api.meetings);const [status,setStatus]=useState('');const [priority,setPriority]=useState('');const [owner,setOwner]=useState('');const [meeting,setMeeting]=useState('');const [deadline,setDeadline]=useState('');const [view,setView]=useState('table');
  const tasks=allTasks(data.value||[]);
  const filtered=tasks.filter(task=>{
    const due=absoluteDeadline(task.deadline);
    const deadlineMatches=!deadline || (deadline==='missing' ? !task.deadline : deadline==='dated' ? due!==null : due!==null && due<Date.now() && task.status!=='completed');
    return (!status||task.status===status) && (!priority||task.priority===priority) && (!owner||(task.owner||'Unassigned')===owner) && (!meeting||task.meeting._id===meeting) && deadlineMatches;
  });
  const selected=data.value?.find(m=>m._id===meeting);
  return <><PageTitle title="From conversation to commitment" description="Every next step, its owner, and what stands in the way."/><section className="panel"><div className="filters"><select aria-label="Filter status" value={status} onChange={e=>setStatus(e.target.value)}><option value="">All statuses</option>{['pending','in progress','completed','blocked'].map(s=><option key={s}>{s}</option>)}</select><select aria-label="Filter priority" value={priority} onChange={e=>setPriority(e.target.value)}><option value="">All priorities</option>{['High','Medium','Low'].map(s=><option key={s}>{s}</option>)}</select><select aria-label="Filter owner" value={owner} onChange={e=>setOwner(e.target.value)}><option value="">All owners</option>{[...new Set(tasks.map(t=>t.owner||'Unassigned'))].map(s=><option key={s}>{s}</option>)}</select><select aria-label="Filter meeting" value={meeting} onChange={e=>setMeeting(e.target.value)}><option value="">All meetings</option>{data.value?.map(m=><option key={m._id} value={m._id}>{m.meeting_title}</option>)}</select><select aria-label="Filter deadline" value={deadline} onChange={e=>setDeadline(e.target.value)}><option value="">All deadlines</option><option value="dated">Calendar date recorded</option><option value="overdue">Overdue calendar date</option><option value="missing">No deadline</option></select><button onClick={data.reload}>Refresh</button></div><div className="tabs"><button className={view==='table'?'active':''} onClick={()=>setView('table')}>Action list</button><button className={view==='graph'?'active':''} onClick={()=>setView('graph')}>Dependency view</button></div>{data.loading?<Loading/>:data.error?<ErrorNotice message={data.error} retry={data.reload}/>:view==='table'?<TaskTable tasks={filtered} onChanged={data.reload}/>:selected?<DependencyGraph graph={selected.graph_analysis} tasks={allTasks([selected])}/>:<p className="empty">Select a meeting above to inspect its dependency graph. Task IDs are scoped to each meeting.</p>}</section></>;
}
