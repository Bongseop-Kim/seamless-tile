import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import server


class ServerApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        server.RUNS_DIR = Path(self.tmp.name) / "runs"
        server.UPLOADS_DIR = Path(self.tmp.name) / "uploads"
        self.client = TestClient(server.app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_upload_run_and_list_runs(self):
        img = Image.new("RGB", (16, 16), "white")
        body = BytesIO()
        img.save(body, format="PNG")
        body.seek(0)

        upload_response = self.client.post(
            "/api/upload",
            files={"file": ("tile.png", body, "image/png")},
        )

        self.assertEqual(upload_response.status_code, 200)
        upload_payload = upload_response.json()
        self.assertEqual(upload_payload["size"], [16, 16])

        uploaded_static = self.client.get(f"/uploads/{upload_payload['image_id']}.png")
        self.assertEqual(uploaded_static.status_code, 200)

        run_response = self.client.post(
            "/api/run",
            json={
                "image_id": upload_payload["image_id"],
                "mask_width": 4,
                "model": "local-preview",
                "prompt": "preserve stripe",
                "strength": 0.7,
            },
        )

        self.assertEqual(run_response.status_code, 200)
        run_payload = run_response.json()
        self.assertIn("run_id", run_payload)
        self.assertIn("final", run_payload["stages"])
        self.assertIn("3x3", run_payload["stages"])

        final_static = self.client.get(run_payload["stages"]["final"])
        self.assertEqual(final_static.status_code, 200)

        runs_response = self.client.get("/api/runs")

        self.assertEqual(runs_response.status_code, 200)
        runs_payload = runs_response.json()
        self.assertEqual(len(runs_payload), 1)
        self.assertEqual(runs_payload[0]["params"]["mask_width"], 4)


if __name__ == "__main__":
    unittest.main()
