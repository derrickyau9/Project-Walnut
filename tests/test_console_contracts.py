"""Cross-language portable recipe and committed-page browser contracts."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

from daos.capability_runtime.recipes import normalize_recipe, loads_recipe, RecipeError

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'Node is optional for JavaScript contract verification')
class ConsoleContractTests(unittest.TestCase):
    def node(self, module, operations):
        script = r'''
import fs from 'node:fs';
const source=fs.readFileSync(process.argv[1],'utf8').replace(/^\uFEFF/,'');
const api=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const operations=JSON.parse(fs.readFileSync(0,'utf8'));
const output=operations.map(operation=>{try {
  if(operation.action==='parse') return {ok:true,value:api.parseRecipe(operation.text)};
  if(operation.action==='encode') return {ok:true,value:api.parseRecipe(api.serializeRecipe(operation.value))};
  return {ok:true,value:api.validateCapturePage(operation.value)};
} catch(error) {return {ok:false};}});
console.log(JSON.stringify(output));
'''
        result = subprocess.run([NODE, '--input-type=module', '-e', script, str(ROOT / module)],
                                input=json.dumps(operations), text=True, capture_output=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_recipe_normalization_matches_offline_cli_contract(self):
        recipes = []
        for kind in ('bracket', 'interval', 'dark', 'flat'):
            plan = {'kind': kind, 'exposures_us': [5000, 10000.0], 'requested_analogue_gain': 1.5}
            if kind == 'interval': plan['interval_ms'] = 250.0
            recipes.append({'format': 'hasslefree.workflow-recipe.v1', 'name': 'Offline recipe', 'plan': plan})
        output = self.node('daos/console/recipes.js', [{'action': 'encode', 'value': value} for value in recipes])
        self.assertEqual([normalize_recipe(value) for value in recipes], [value['value'] for value in output])
        self.assertTrue(all(value['ok'] for value in output))

    def test_recipe_rejects_duplicate_secret_complex_and_oversized_fields(self):
        base = {'format': 'hasslefree.workflow-recipe.v1', 'name': 'Offline', 'plan': {'exposures_us': [5000]}}
        invalid = ['\ufeff' + json.dumps(base), json.dumps(base) + 'x', ' ' * 65537,
                   '{"format":"hasslefree.workflow-recipe.v1","format":"hasslefree.workflow-recipe.v1","name":"x","plan":{"exposures_us":[5000]}}',
                   '{"format":"hasslefree.workflow-recipe.v1","name":"x","plan":{"exposures_us":[5000],"exposures_us":[6000]}}',
                   json.dumps({**base, 'token': 'never-export-this-token'}),
                   json.dumps({**base, 'job_id': 'not-a-recipe-field'}),
                   json.dumps({**base, 'name': '\u200bhidden'}), json.dumps({**base, 'name': ' needs trimming '}),
                   json.dumps({**base, 'plan': {'exposures_us': [True]}}),
                   json.dumps({**base, 'plan': {'exposures_us': [5000], 'file_path': '/private/photo.dng'}}),
                   json.dumps({**base, 'plan': {'exposures_us': [5000], 'requested_analogue_gain': float('nan')}}),
                   json.dumps({**base, 'plan': {'exposures_us': [5000], 'interval_ms': 10}})]
        result = self.node('daos/console/recipes.js', [{'action': 'parse', 'text': text} for text in invalid])
        self.assertTrue(all(not value['ok'] for value in result))
        for value in invalid:
            with self.subTest(value=value[:60]), self.assertRaises((RecipeError, ValueError)):
                loads_recipe(value)

    def test_retained_page_is_bounded_allowlisted_and_truthful(self):
        rid = str(uuid.uuid4())
        item = {'request_id': rid, 'capture_id': rid, 'frame_id': rid, 'status': 'complete',
                'filename': rid + '.dng', 'sensor_mode': 'sim-small', 'width': 128, 'height': 96,
                'bit_depth': 14, 'cfa': 'RGGB', 'simulated': True,
                'actual_exposure_us': 10000, 'actual_analogue_gain': 1.0,
                'token': 'not-exposed-to-DOM', 'file_path': '/not-a-browser-path'}
        page = {'items': [item], 'next_cursor': None, 'limit': 20, 'order': 'newest_first'}
        invalid = [{**page, 'items': [item] * 21}, {**page, 'items': [item, item]},
                   {**page, 'next_cursor': '../bad'}, {**page, 'items': [{**item, 'status': 'unknown'}]},
                   {**page, 'items': [{**item, 'actual_exposure_us': 10**30}]}]
        result = self.node('daos/console/retained.js', [{'action': 'page', 'value': page}] +
                           [{'action': 'page', 'value': value} for value in invalid])
        self.assertTrue(result[0]['ok'])
        self.assertNotIn('token', result[0]['value']['items'][0])
        self.assertNotIn('file_path', result[0]['value']['items'][0])
        self.assertTrue(all(not value['ok'] for value in result[1:]))


class WorkflowListContractTests(unittest.TestCase):
    def test_recent_jobs_are_bounded_without_deleting_older_history(self):
        from daos.capability_runtime.manager import WorkflowManager
        from daos.capability_runtime.bracket import WorkflowError
        with tempfile.TemporaryDirectory() as temporary:
            manager = WorkflowManager(None, Path(temporary) / 'workflow.sqlite3')
            with manager.runner._db() as db:
                db.executemany('INSERT INTO jobs VALUES(?,?,NULL,?,NULL)',
                               [('job' + str(index), '{}', 'completed') for index in range(55)])
            jobs = manager.list_jobs()
            self.assertEqual(50, len(jobs))
            self.assertEqual('job54', jobs[0]['job_id'])
            self.assertEqual('job5', jobs[-1]['job_id'])
            self.assertEqual(['job54', 'job53', 'job52'], [job['job_id'] for job in manager.list_jobs(3)])
            self.assertEqual('job0', manager.inspect('job0')['job_id'])
            for invalid in (True, 0, 101, '10'):
                with self.subTest(limit=invalid), self.assertRaises(WorkflowError):
                    manager.list_jobs(invalid)
            with manager.runner._db() as db:
                self.assertEqual(55, db.execute('SELECT count(*) FROM jobs').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
