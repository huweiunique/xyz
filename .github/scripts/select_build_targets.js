/* 手动按端选择；定时运行逐端对照最近一次实际成功构建。 */
const {execFileSync} = require('node:child_process');
const definitions = {
  server: {job:'docker', step:'构建并推送（linux/amd64 + linux/arm64）'},
  android: {job:'apk', step:'构建 APK'},
  ios: {job:'ipa', step:'构建未签名 IPA'}
};
function affected(target, paths) {
  return paths.some(path => {
    if (target === 'server') return /^(proxy\/|web\/|local_ext\/|docker\/|Dockerfile$|docker-compose\.yml$)/.test(path);
    if (target === 'android') return /^android\//.test(path) || ['web/index.html','web/hls.min.js'].includes(path);
    return /^ios\//.test(path) || ['local_ext/app_settings.js','local_ext/prefip_patch.js','tools/stamp_ios_build.py'].includes(path);
  });
}
function changedFiles(base, head) {
  if (!/^[0-9a-f]{40}$/.test(base) || !/^[0-9a-f]{40}$/.test(head)) throw Error('构建提交号无效');
  return execFileSync('git',['diff','--name-only','--no-renames',base,head],{encoding:'utf8'}).trim().split('\n').filter(Boolean);
}
async function select({github, context, core, scope, diff=changedFiles}) {
  const explicit={server:['server'],android:['android'],ios:['ios'],apps:['android','ios'],all:Object.keys(definitions)};
  let targets;
  if (scope !== 'auto') {
    if (!Object.hasOwn(explicit,scope)) throw Error('未知构建范围：'+scope);
    targets=explicit[scope];
  } else {
    const baselines={}, latestFailed={};
    const branch=context.ref.replace(/^refs\/heads\//,'');
    const runs=await github.paginate(github.rest.actions.listWorkflowRuns,{
      ...context.repo,workflow_id:'nightly.yml',branch,status:'completed',per_page:100
    });
    for (const run of runs) {
      const jobs=await github.paginate(github.rest.actions.listJobsForWorkflowRun,{
        ...context.repo,run_id:run.id,filter:'latest',per_page:100
      });
      for (const [target,definition] of Object.entries(definitions)) {
        if (baselines[target]) continue;
        const job=jobs.find(job=>job.name===definition.job || job.name.endsWith(' / '+definition.job));
        const built=job?.conclusion==='success' && job.steps?.some(step=>step.name===definition.step && step.conclusion==='success');
        const published=target==='server' || jobs.some(job=>(job.name==='publish' || job.name.endsWith(' / publish')) && job.conclusion==='success');
        if (job && job.conclusion && job.conclusion!=='skipped' && !(target in latestFailed)) {
          latestFailed[target]=!built || !published;
        }
        if (built && published) {
          baselines[target]=run.head_sha;
        }
      }
      if (Object.keys(baselines).length===3) break;
    }
    targets=Object.keys(definitions).filter(target=>{
      if (latestFailed[target]) {core.info(target+' 最近构建未成功，保留重试资格');return true;}
      const base=baselines[target];
      if (!base) {core.info(target+' 尚无成功构建，纳入本次目标');return true;}
      try {
        const changed=affected(target,diff(base,context.sha));
        core.info(target+(changed?' 有依赖变更，将构建':' 无依赖变更，跳过'));
        return changed;
      } catch (_) {
        core.warning(target+' 无法核对历史提交，保留构建资格');return true;
      }
    });
  }
  const value=targets.join(',') || 'none';
  core.setOutput('targets',value);
  core.info('本次构建目标：'+value);
  return targets;
}
module.exports=select;
