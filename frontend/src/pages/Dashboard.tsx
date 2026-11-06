import { Link } from 'react-router-dom';
import { ArrowUpRight, CalendarDays } from 'lucide-react';
import { api } from '../services/api';
import { tasksOf, taskStatus, absoluteDeadline, dateLabel } from '../services/data';
import { PageTitle, useLoad, Loading, ErrorNotice, MeetingTable, Empty } from '../components/ui';
import { NewMeetingLink } from '../components/ui';
export default function Dashboard() {
  const data=useLoad(async()=>({meetings:await api.meetings(),minutes:await api.minutesList()}));
  if (data.loading) return <Loading/>;
  return <><PageTitle eyebrow="THE BIG PICTURE" title="Your meetings, made meaningful." description="Decisions remembered. Responsibilities clear. Work moving forward." action={<NewMeetingLink/>}/>{data.error && <ErrorNotice message={data.error} retry={data.reload}/>}{data.value && (()=>{
    const {meetings,minutes}=data.value; const tasks=meetings.flatMap(m=>tasksOf(m).map(t=>({...t,meeting:m})));
    const stats=[['Meetings',meetings.length],['Processed',meetings.filter(m=>m.status==='completed').length],['Action items',tasks.length],['Pending',tasks.filter(t=>taskStatus(t)==='pending').length],['Completed',tasks.filter(t=>taskStatus(t)==='completed').length],['Blocked',tasks.filter(t=>taskStatus(t)==='blocked').length]];
    const upcoming=tasks.filter(t=>{const d=absoluteDeadline(t.deadline); return d!==null && d>=new Date().setHours(0,0,0,0) && t.status!=='completed';}).sort((a,b)=>absoluteDeadline(a.deadline)!-absoluteDeadline(b.deadline)!).slice(0,5);
    const decisions=[...minutes].sort((a,b)=>b.created_at.localeCompare(a.created_at)).flatMap(r=>r.minutes.decisions.map(c=>({...c,revision:r}))).slice(0,4);
    return <><div className="stats-strip">{stats.map(([label,value])=><div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div><section className="panel"><div className="section-title"><div><p className="eyebrow">THE CONVERSATION LOG</p><h2>Recent meetings</h2></div><Link to="/meetings">All meetings <ArrowUpRight size={16}/></Link></div><MeetingTable meetings={meetings.slice(0,5)} minutes={minutes}/></section><div className="two-column"><section className="panel"><div className="section-title"><h2>On the horizon</h2><CalendarDays size={19}/></div>{upcoming.length ? upcoming.map(t=><Link className="list-row" key={t.meeting._id+t.task_id} to={'/meetings/'+t.meeting._id}><div><strong>{t.description}</strong><small>{t.owner || 'Owner not recorded'} ? {t.meeting.meeting_title}</small></div><span className="date-chip">{dateLabel(t.deadline)}</span></Link>):<Empty title="No upcoming dated tasks">Only explicit calendar dates are included here; relative deadlines remain in Action Items.</Empty>}</section><section className="panel"><div className="section-title"><h2>Decisions worth remembering</h2></div>{decisions.length ? decisions.map((c,i)=><Link className="list-row" key={i} to={'/meetings/'+c.revision.meeting_id}><div><strong>{c.text}</strong><small>{c.revision.minutes.title} ? {c.revision.status} ? {c.assessment.replaceAll('_',' ')}</small></div><ArrowUpRight size={17}/></Link>):<Empty title="Your decisions will live here">Generate meeting minutes to build a shared record.</Empty>}</section></div></>;
  })()}</>;
}
