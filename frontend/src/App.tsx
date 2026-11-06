import { NavLink, Link, Route, Routes, useLocation } from 'react-router-dom';
import { BookOpen, LayoutDashboard, CalendarDays, ListChecks, MessageSquare, Activity, ArrowUpRight } from 'lucide-react';
import { api } from './services/api';
import { useLoad } from './components/ui';
import Dashboard from './pages/Dashboard';
import Meetings from './pages/Meetings';
import MeetingDetail from './pages/MeetingDetail';
import Tasks from './pages/Tasks';
import Assistant from './pages/Assistant';
import Monitoring from './pages/Monitoring';
const navigation = [{to:'/',label:'Dashboard',icon:LayoutDashboard},{to:'/meetings',label:'Meetings',icon:CalendarDays},{to:'/tasks',label:'Action Items',icon:ListChecks},{to:'/assistant',label:'Meeting Assistant',icon:MessageSquare},{to:'/monitoring',label:'Monitoring',icon:Activity}];
export default function App() {
  const location = useLocation(); const connection = useLoad(api.health, [location.pathname]);
  return <div className="app"><a className="skip-link" href="#main">Skip to content</a><aside className="sidebar"><Link className="brand" to="/"><span className="brand-icon"><BookOpen size={24}/></span><span>Minutes<span className="brand-sub">MEETING INTELLIGENCE</span></span></Link><p className="nav-label">WORKSPACE</p><nav aria-label="Main navigation">{navigation.map(({to,label,icon:Icon}) => <NavLink to={to} end={to==='/'} key={to}><Icon size={19}/>{label}</NavLink>)}</nav><div className="sidebar-footer"><span className={'connection-dot '+(connection.value?.database_connected ? 'connected':'')}/><div><strong>{connection.value?.database_connected ? 'Atlas connected' : connection.loading ? 'Connecting?' : 'Backend unavailable'}</strong><small>{connection.value?.configured ? connection.value.provider+' ? '+connection.value.model : 'Check API configuration'}</small></div><button className="icon-button" aria-label="Refresh connection" onClick={connection.reload}><Activity size={15}/></button></div></aside><div className="workspace"><div className="topbar"><span>Workspace <span className="breadcrumb">/ {location.pathname.startsWith('/meetings/') ? 'Meeting record' : navigation.find(n => n.to===location.pathname)?.label || 'Meeting intelligence'}</span></span><span className="workspace-label">A considered record of every conversation</span></div><main id="main"><Routes><Route path="/" element={<Dashboard/>}/><Route path="/meetings" element={<Meetings/>}/><Route path="/meetings/:meetingId" element={<MeetingDetail/>}/><Route path="/tasks" element={<Tasks/>}/><Route path="/assistant" element={<Assistant/>}/><Route path="/monitoring" element={<Monitoring/>}/><Route path="*" element={<div className="empty"><h1>Page not found</h1><Link to="/">Return to dashboard <ArrowUpRight size={16}/></Link></div>}/></Routes></main><footer className="app-footer"><span>Minutes / Meeting intelligence workspace</span><span>Evidence first. Human reviewed.</span></footer></div></div>;
}
