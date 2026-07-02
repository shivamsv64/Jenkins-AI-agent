import unittest
from unittest.mock import patch

import requests

from jenkins_client import JenkinsClient


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class JenkinsClientMultiUrlTests(unittest.TestCase):
    def test_list_jobs_tries_next_url_when_first_fails(self):
        client = JenkinsClient(
            url="http://jenkins-a:8080, http://jenkins-b:8080",
            username="user",
            api_token="token",
            cfg={},
        )

        calls = []

        def fake_request(method, url, **kwargs):
            calls.append(url)
            if "jenkins-a" in url:
                raise requests.exceptions.ConnectionError("down")
            return DummyResponse({"jobs": []})

        with patch.object(client.session, "request", side_effect=fake_request):
            jobs = client.list_jobs()

        self.assertEqual(jobs, [])
        self.assertEqual(calls[0], "http://jenkins-a:8080/api/json")
        self.assertEqual(calls[1], "http://jenkins-b:8080/api/json")


if __name__ == "__main__":
    unittest.main()
