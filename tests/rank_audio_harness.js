/* 在最小 DOM 边界执行实际业务函数，捕获错误编号和意外开声回归。 */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('web/index.html', 'utf8');
function source(name) {
  const start = html.search(new RegExp('(?:async )?function ' + name + '\\('));
  assert(start >= 0, '找不到业务函数 ' + name);
  for (let end = html.indexOf('}', start); end >= 0; end = html.indexOf('}', end + 1)) {
    const value = html.slice(start, end + 1);
    try { new vm.Script(value); return value; } catch (_) {}
  }
  throw Error('函数无法解析：' + name);
}
function media() {
  return {muted:false, defaultMuted:false, paused:false, src:'fixture',
    play(){this.paused=false;return Promise.resolve();}, pause(){this.paused=true;},
    setAttribute(){}, addEventListener(){}};
}
function context() {
  const feed = media(), player = media(), values = {hg_muted:'false'};
  const nodes = {};
  const doc = {
    querySelector: selector => selector === '#video' ? player :
      selector.includes('.slide') ? {querySelector:()=>feed} : nodes[selector],
    querySelectorAll: selector => selector.includes('video') ? [feed,player] : [],
    getElementById: id => nodes['#'+id],
    addEventListener() {},
  };
  const c = vm.createContext({console, document:doc, Number, Promise, setTimeout, clearTimeout});
  c.window=c; c.$=doc.querySelector; c.$$=doc.querySelectorAll;
  c.addEventListener=()=>{};
  c.store={get:(k,d)=>k in values?JSON.parse(values[k]):d,
    set:(k,v)=>{values[k]=JSON.stringify(v);}};
  c.updateMuteBtns=()=>{};c.showCenterFlash=()=>{};c.toast=()=>{};
  c.runToken={rank:0};c.esc=s=>String(s??'').replace(/[&<>\"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
  c.fmtN=n=>String(n);c.coverImg=()=>'<img>';
  c.itemsOf=j=>j.data.items;c.bindClicks=box=>{
    box.rows=[...box.innerHTML.matchAll(/data-id="([^"]+)"/g)].map(m=>({
      click:()=>c.openDetail(+m[1]), id:m[1]}));
  };
  c.openDetail=id=>{c.opened=id;};
  for(const file of ['audio_patch.js','rank_patch.js']) {
    if(fs.existsSync('local_ext/'+file))vm.runInContext(fs.readFileSync('local_ext/'+file,'utf8'),c);
  }
  return {c,feed,player,values,nodes};
}
async function main() {
  const {c,feed,player,values,nodes}=context();
  if(process.argv[2]==='rank') {
    const box=nodes['#rankList']={innerHTML:'',setAttribute(){},removeAttribute(){},querySelectorAll:()=>[]};
    const tabs=nodes['#rankTabs']={innerHTML:'',querySelectorAll:()=>[],setAttribute(){}};
    if(c.HGRanks)c.HGRanks.init();
    assert(tabs.innerHTML.includes('recommend') && tabs.innerHTML.includes('potential'),'榜单必须跟随源站三个参数');
    assert(!tabs.innerHTML.includes('weekly'),'不能保留无效榜单');
    vm.runInContext(source('loadRank'),c);
    const calls=[];
    c.api=async path=>{calls.push(path);return {status:1,data:{items:[
      {video_id:2833,id:999,rank:3,title:'<测试>',metric_label:'推荐指数',metric_value:5555},
      {id:12,title:'兼容旧条目'}, {video_id:'bad',title:'无效编号'}]}};};
    await c.loadRank('recommend');
    assert.equal(calls[0],'/api/ranks/recommend');
    assert.equal(box.rows[0].id,'2833','榜单应优先使用 video_id，不能使用条目 id');
    box.rows[0].click();assert.equal(c.opened,2833);
    assert.equal(box.rows.length,2,'无效编号不生成可点击项');
    assert(box.innerHTML.includes('推荐指数') && box.innerHTML.includes('5555'));
    assert(box.innerHTML.includes('&lt;测试&gt;'));
    let rejectOld;
    c.api=()=>new Promise((_,reject)=>{rejectOld=reject;});
    const old=c.loadRank('hot');
    c.api=async()=>({status:1,data:{items:[{video_id:12,title:'新榜单'}]}});
    await c.loadRank('potential');rejectOld(Error('旧请求失败'));await old;
    assert(box.innerHTML.includes('新榜单'),'迟到的失败不能覆盖当前榜单');
    c.api=async()=>({status:1,data:{items:[]}});await c.loadRank('hot');
    assert(box.innerHTML.includes('暂无数据'));
  } else if(process.argv[2]==='audio') {
    if(c.HGAudio)c.HGAudio.install(c.store);
    assert.equal(c.store.get('hg_muted',true),true,'旧的开声记录不能影响新会话');
    assert.equal(player.muted,true,'详情播放器首次也必须静音');
    assert.equal(feed.muted,true);
    vm.runInContext(source('toggleSlidePause'),c);
    c.toggleSlidePause(12);assert.equal(feed.muted,true,'暂停不能开声');
    c.toggleSlidePause(12);assert.equal(feed.muted,true,'继续不能开声');
    vm.runInContext(source('toggleMute'),c);c.toggleMute();
    assert.equal(feed.muted,false);assert.equal(player.muted,false,'首页/详情共享本次选择');
    assert.equal(values.hg_muted,'false','声音选择不写入持久存储');
    c.HGAudio.toggle();assert.equal(feed.muted,true);assert.equal(player.muted,true);
    c.store.set('hg_line','proxy');assert.equal(c.store.get('hg_line'),'proxy','其他设置照常存储');
    const next=context();next.c.HGAudio.install(next.c.store);
    assert.equal(next.player.muted,true,'重新打开恢复静音');
  } else {
    assert(html.includes('src="/local_ext/audio_patch.js"'));
    const init=html.indexOf('window.HGAudio.install(store)');
    assert(init>0 && init<html.indexOf('\nloadHome();'),'必须在启动加载前初始化静音');
    assert(init<html.indexOf('\nhandleDeepLink();'),'深链接也必须静音起播');
    assert(html.indexOf('src="/local_ext/audio_patch.js"')<init);
    assert.equal(html,fs.readFileSync('android/app/src/main/assets/hghub/index.html','utf8'));
  }
  console.log('PASS '+process.argv[2]);
}
main().catch(error=>{console.error(error);process.exitCode=1;});
