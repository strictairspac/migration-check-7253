import {api,post} from './client.js';
const qs=v=>v==null||v===''?'':'?version='+encodeURIComponent(v);
export const batches={list:()=>api('/batches'),save:(d,id)=>post('/batches'+(id?'/'+id:''),d,id?'PUT':'POST')};
export const mappings={list:()=>api('/mappings'),save:(d,id)=>post('/mappings'+(id?'/'+id:''),d,id?'PUT':'POST')};
export const runs={
 list:()=>api('/runs'),run:d=>post('/runs',d),cancel:id=>post('/runs/'+id+'/cancel',{}),
 versions:id=>api('/runs/'+id+'/versions'),version:(id,v)=>api('/runs/'+id+'/versions/'+v),
 correct:(id,body)=>post('/runs/'+id+'/corrections',body),
 targets:(id,v)=>api('/runs/'+id+'/targets'+qs(v)),restore:(id,backup)=>post('/runs/'+id+'/restore',{backup})
};
