import { afterEach, describe, expect, it } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Claims, MeetingTable, ErrorNotice, Loading } from './ui';
afterEach(cleanup);
describe('accessible meeting states',()=>{
 it('renders source excerpts and exact timestamp',()=>{render(<Claims items={[{text:'Ship Friday',assessment:'ambiguous',evidence:[{segment_id:'seg-1',timestamp:'00:12',excerpt:'We might ship Friday.'}]}]}/>);expect(screen.getByText('We might ship Friday.')).toBeInTheDocument();expect(screen.getByText(/00:12/)).toBeInTheDocument();});
 it('shows an empty library rather than invented meetings',()=>{render(<MemoryRouter><MeetingTable meetings={[]}/></MemoryRouter>);expect(screen.getByText('A clear record starts with a meeting')).toBeInTheDocument();});
 it('uses accessible loading and failure messages',()=>{render(<><Loading/><ErrorNotice message="Atlas unavailable"/></>);expect(screen.getByRole('status')).toBeInTheDocument();expect(screen.getByRole('alert')).toHaveTextContent('Atlas unavailable');});
});


it('offers the optional meeting delete action',()=>{
 render(<MemoryRouter><MeetingTable meetings={[{_id:'m',meeting_title:'A meeting'}]} onDelete={()=>{}}/></MemoryRouter>);
 expect(screen.getByRole('button',{name:'Delete A meeting'})).toBeInTheDocument();
});
