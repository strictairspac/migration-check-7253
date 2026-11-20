import {batches,mappings,runs} from '../api/business.js';
export const store={batches:[],mappings:[],runs:[],user:null,versions:{},views:{},selectedVersion:{}};
export async function refresh(){
 [store.batches,store.mappings,store.runs]=await Promise.all([batches.list(),mappings.list(),runs.list()]);
}
export async function loadVersions(runId){
 if(runId==null)return null;
 let summary=await runs.versions(runId);
 store.versions[runId]=summary;
 let want=store.selectedVersion[runId]??summary.current_version;
 if(want==null||!summary.versions.some(v=>v.version===want))want=summary.current_version;
 store.selectedVersion[runId]=want;
 store.views[runId]=want==null?null:await runs.version(runId,want);
 return store.views[runId];
}
export async function selectVersion(runId,version){
 store.selectedVersion[runId]=version;
 store.views[runId]=await runs.version(runId,version);
 store.versions[runId]=await runs.versions(runId);
}
