import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
from PIL import Image


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        bridge.DATA = Path(cls.temp.name)
        bridge.prepare()
        cls.server = bridge.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
        cls.server.instance = 'test-instance'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.image = bridge.DATA / 'reference.png'
        Image.new('RGB', (160, 90), 'blue').save(cls.image)
        cls.video = bridge.DATA / 'fixture.mp4'
        subprocess.run([bridge.executable('ffmpeg'), '-v', 'error', '-f', 'lavfi',
                        '-i', 'color=c=blue:s=160x90:d=0.5', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', str(cls.video)], check=True)
        cls.other_video = bridge.DATA / 'other-fixture.mp4'
        subprocess.run([bridge.executable('ffmpeg'), '-v', 'error', '-f', 'lavfi',
                        '-i', 'color=c=red:s=160x90:d=0.5', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', str(cls.other_video)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def job(self):
        with patch.object(bridge, 'ensure_service', return_value={'public_url': self.base}):
            return bridge.create_job('Animate the blue shape', [str(self.image)])

    def request(self, url, data=None, method=None):
        try:
            req = urllib.request.Request(url, data=data, method=method)
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_reference_auth_is_job_scoped_and_records_real_download(self):
        job = self.job(); other = self.job()
        payload = bridge.bot_payload(job)
        url = payload['image_urls'][0]
        wrong = url.replace(job['read_token'], other['read_token'])
        self.assertEqual(self.request(wrong)[0], 403)
        self.assertEqual(self.request(url.split('?')[0])[0], 403)
        code, content = self.request(url)
        self.assertEqual(code, 200)
        self.assertEqual(content, self.image.read_bytes())
        # The HTTP writer can finish the client response immediately before recording the event.
        import time
        for _ in range(20):
            events = bridge.get_job(job['job_id'])['events']
            if any(x['event'] == 'reference_downloaded' for x in events): break
            time.sleep(0.02)
        self.assertTrue(any(x['event'] == 'reference_downloaded' for x in events))

    def test_success_requires_a_decodable_uploaded_video(self):
        job = self.job(); payload = bridge.bot_payload(job)
        code, _ = self.request(payload['status_url'], b'{"status":"succeeded"}', 'POST')
        self.assertEqual(code, 400)
        self.assertNotEqual(bridge.get_job(job['job_id'])['status'], 'succeeded')
        code, _ = self.request(payload['result_upload_url'], b'not an mp4', 'PUT')
        self.assertEqual(code, 400)
        code, raw = self.request(payload['result_upload_url'], self.video.read_bytes(), 'PUT')
        self.assertEqual(code, 200, raw)
        saved = bridge.get_job(job['job_id'])
        self.assertEqual(saved['status'], 'succeeded')
        self.assertEqual(saved['video']['width'], 160)
        self.assertFalse(saved['video']['has_audio'])
        self.assertEqual(self.request(payload['result_upload_url'], self.video.read_bytes(), 'PUT')[0], 200)
        result = bridge.download_video(job['job_id'], str(bridge.DATA / 'download'))
        self.assertEqual(bridge.sha256(result['path']), saved['video']['sha256'])

    def test_uncertain_webhook_transport_does_not_trigger_a_second_generation(self):
        with patch.object(bridge, 'config', return_value={'webhook_url': 'https://example.invalid/webhook', 'webhook_key': 'fixture'}), \
             patch.object(bridge, 'ensure_service', return_value={'public_url': self.base}), \
             patch.object(bridge.urllib.request, 'urlopen', side_effect=urllib.error.URLError('timeout')) as invoke:
            result = bridge.submit_video('Text-only test')
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(result['status'], 'submission_unknown')
        self.assertIn('do not automatically resubmit', result['error'])

    def test_public_results_exclude_capability_tokens(self):
        job = self.job()
        job['error'] = 'Callback failed: https://example.invalid/?token=' + job['write_token']
        job['events'].append({'message': 'Image URL token=' + job['read_token']})
        rendered = json.dumps(bridge.public_job(job))
        self.assertNotIn(job['read_token'], rendered)
        self.assertNotIn(job['write_token'], rendered)

    def test_malformed_callback_is_rejected_without_changing_state(self):
        job = self.job()
        code, _ = self.request(bridge.bot_payload(job)['status_url'], b'[]', 'POST')
        self.assertEqual(code, 400)
        self.assertEqual(bridge.get_job(job['job_id'])['status'], 'prepared')

    def test_conflicting_concurrent_uploads_cannot_overwrite_a_result(self):
        job = self.job()
        url = bridge.bot_payload(job)['result_upload_url']
        files = [self.video, self.other_video]
        barrier = threading.Barrier(2)
        def upload(path):
            barrier.wait(timeout=5)
            return self.request(url, path.read_bytes(), 'PUT')[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(upload, files))
        self.assertEqual(sorted(codes), [200, 400])
        saved = bridge.get_job(job['job_id'])
        self.assertEqual(saved['status'], 'succeeded')
        self.assertIn(saved['video']['sha256'], [bridge.sha256(p) for p in files])
        self.assertEqual(bridge.sha256(saved['video']['path']), saved['video']['sha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
