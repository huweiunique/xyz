"""执行真实构建范围选择器，覆盖逐端基线、重试及共享资源依赖。"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '未安装 Node.js')
class BuildTargetTests(unittest.TestCase):
    def execute(self, scope='auto', runs=None, jobs=None, files=None):
        payload=dict(scope=scope,runs=runs or [],jobs=jobs or {},files=files or {})
        js=r"""
        let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
          const p=JSON.parse(input),outputs={},bases=[],actions={listWorkflowRuns(){},listJobsForWorkflowRun(){}};
          const github={rest:{actions},paginate:async(method,args)=>method===actions.listWorkflowRuns?p.runs:(p.jobs[args.run_id]||[])};
          const select=require('./.github/scripts/select_build_targets.js');
          const targets=await select({github,context:{repo:{owner:'test',repo:'test'},ref:'refs/heads/main',sha:'current'},
            core:{setOutput:(k,v)=>outputs[k]=v,info(){},warning(){}},scope:p.scope,
            diff:base=>{bases.push(base);return p.files[base]||[];}});
          console.log(JSON.stringify({targets,outputs,bases}));
        });
        """
        result=subprocess.run(['node','-e',js],input=json.dumps(payload),cwd=ROOT,
                              capture_output=True,text=True,encoding='utf-8',timeout=10,check=True)
        return json.loads(result.stdout)

    def jobs(self, target=None, conclusion='success'):
        names={'server':('docker','构建并推送（linux/amd64 + linux/arm64）'),
               'android':('apk','构建 APK'),'ios':('ipa','构建未签名 IPA')}
        jobs = [dict(name='build / '+job,conclusion=conclusion,
                     steps=[dict(name=step,conclusion=conclusion)])
                for key,(job,step) in names.items() if target is None or target==key]
        if target != 'server':
            jobs.append(dict(name='build / publish',conclusion=conclusion))
        return jobs

    def test_explicit_server_never_selects_apps(self):
        result=self.execute('server')
        self.assertEqual(result['targets'],['server'])
        self.assertEqual(result['outputs']['targets'],'server')
        self.assertEqual(result['bases'],[])

    def test_each_target_uses_its_own_last_success(self):
        result=self.execute(runs=[dict(id=2,head_sha='server-new'),dict(id=1,head_sha='apps-old')],
                            jobs={'2':self.jobs('server'),'1':self.jobs()},
                            files={'server-new':[],'apps-old':['local_ext/viewing_ui.js']})
        self.assertEqual(result['targets'],[])
        self.assertEqual(result['outputs']['targets'],'none')
        self.assertEqual(result['bases'],['server-new','apps-old','apps-old'])

    def test_shared_resources_follow_actual_packaging(self):
        for paths,expected in [(['web/index.html'],['server','android']),
                               (['local_ext/prefip_patch.js'],['server','ios']),
                               (['tools/stamp_ios_build.py'],['ios']),
                               (['docs/release.md'],[])]:
            with self.subTest(paths=paths):
                result=self.execute(runs=[dict(id=1,head_sha='base')],jobs={'1':self.jobs()},files={'base':paths})
                self.assertEqual(result['targets'],expected)

    def test_failed_target_retries_even_without_more_source_changes(self):
        result=self.execute(runs=[dict(id=2,head_sha='failed'),dict(id=1,head_sha='base')],
                            jobs={'2':self.jobs('android','failure'),'1':self.jobs()})
        self.assertEqual(result['targets'],['android'])

    def test_first_build_includes_all_targets(self):
        self.assertEqual(self.execute()['targets'],['server','android','ios'])

    def test_packages_that_failed_to_publish_remain_retryable(self):
        jobs=self.jobs()
        jobs[-1]['conclusion']='failure'
        result=self.execute(runs=[dict(id=2,head_sha='not-published'),dict(id=1,head_sha='base')],
                            jobs={'2':jobs,'1':self.jobs()})
        self.assertEqual(result['targets'],['android','ios'])
