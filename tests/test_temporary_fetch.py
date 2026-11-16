import os
import unittest

try:
    from unittest import mock
except ImportError:
    import mock

import requests
import responses

from openbadges.verifier.actions.tasks import add_task
from openbadges.verifier.fetch_policy import (FETCH_TIMEOUT_ENV, FetchNotAllowedError, TEMPORARY_FETCH_ERROR,
                                              apply_fetch_policy, get_fetch_timeout, is_temporary_http_status)
from openbadges.verifier.tasks import task_named
from openbadges.verifier.tasks.graph import fetch_http_node
from openbadges.verifier.tasks.task_types import FETCH_HTTP_NODE, IMAGE_VALIDATION

ASSERTION_URL = 'https://lms.example.org/badges/assertion.php?b=abc'
IMAGE_URL = 'https://lms.example.org/badges/image.png'


class TemporaryHttpStatusTests(unittest.TestCase):
    def test_temporary_statuses(self):
        for status in (408, 429, 500, 502, 503, 504):
            self.assertTrue(is_temporary_http_status(status), status)

    def test_non_temporary_statuses(self):
        for status in (None, 200, 301, 400, 403, 404, 410):
            self.assertFalse(is_temporary_http_status(status), status)


class FetchHttpNodeTemporaryErrorTests(unittest.TestCase):
    @responses.activate
    def test_service_unavailable_is_reported_as_temporary(self):
        responses.add(responses.GET, ASSERTION_URL, body='<html>maintenance</html>', status=503,
                      content_type='text/html')
        success, message, actions = fetch_http_node({}, add_task(FETCH_HTTP_NODE, url=ASSERTION_URL))
        self.assertFalse(success)
        self.assertTrue(message.startswith(TEMPORARY_FETCH_ERROR), message)
        self.assertIn('503', message)

    @responses.activate
    def test_json_error_body_with_5xx_is_still_temporary(self):
        responses.add(responses.GET, ASSERTION_URL, json={'error': 'down'}, status=502)
        success, message, actions = fetch_http_node({}, add_task(FETCH_HTTP_NODE, url=ASSERTION_URL))
        self.assertFalse(success)
        self.assertTrue(message.startswith(TEMPORARY_FETCH_ERROR), message)
        self.assertEqual(actions, [])

    @responses.activate
    def test_not_found_is_not_temporary(self):
        responses.add(responses.GET, ASSERTION_URL, body='<html>not found</html>', status=404,
                      content_type='text/html')
        success, message, actions = fetch_http_node({}, add_task(FETCH_HTTP_NODE, url=ASSERTION_URL))
        self.assertFalse(success)
        self.assertNotIn(TEMPORARY_FETCH_ERROR, message)


class ImageTemporaryErrorTests(unittest.TestCase):
    def run_image_task(self):
        badgeclass = {'id': 'https://lms.example.org/badges/badge_json.php?id=1', 'image': IMAGE_URL}
        state = {'graph': [badgeclass]}
        task_meta = add_task(IMAGE_VALIDATION, node_id=badgeclass['id'], prop_name='image')
        return task_named(IMAGE_VALIDATION)(state, task_meta)

    @responses.activate
    def test_image_service_unavailable_is_temporary(self):
        responses.add(responses.GET, IMAGE_URL, body='', status=503)
        success, message, actions = self.run_image_task()
        self.assertFalse(success)
        self.assertTrue(message.startswith(TEMPORARY_FETCH_ERROR), message)

    @responses.activate
    def test_image_connection_error_is_temporary(self):
        responses.add(responses.GET, IMAGE_URL, body=requests.ConnectionError('refused'))
        success, message, actions = self.run_image_task()
        self.assertFalse(success)
        self.assertTrue(message.startswith(TEMPORARY_FETCH_ERROR), message)

    @responses.activate
    def test_image_timeout_is_temporary(self):
        responses.add(responses.GET, IMAGE_URL, body=requests.ReadTimeout('slow'))
        success, message, actions = self.run_image_task()
        self.assertFalse(success)
        self.assertTrue(message.startswith(TEMPORARY_FETCH_ERROR), message)

    @responses.activate
    def test_image_not_found_is_not_temporary(self):
        responses.add(responses.GET, IMAGE_URL, body='', status=404)
        success, message, actions = self.run_image_task()
        self.assertFalse(success)
        self.assertNotIn(TEMPORARY_FETCH_ERROR, message)

    @responses.activate
    def test_image_fetch_not_allowed_is_not_temporary(self):
        responses.add(responses.GET, IMAGE_URL, body=FetchNotAllowedError('not allowed'))
        success, message, actions = self.run_image_task()
        self.assertFalse(success)
        self.assertNotIn(TEMPORARY_FETCH_ERROR, message)
        self.assertIn('FetchNotAllowedError', message)


class FetchHttpNodeNetworkErrorTests(unittest.TestCase):
    @responses.activate
    def test_timeout_is_reported_as_temporary(self):
        responses.add(responses.GET, ASSERTION_URL, body=requests.ReadTimeout('slow'))
        success, message, actions = fetch_http_node({}, add_task(FETCH_HTTP_NODE, url=ASSERTION_URL))
        self.assertFalse(success)
        self.assertTrue(message.startswith(TEMPORARY_FETCH_ERROR), message)
        self.assertIn('ReadTimeout', message)

    @responses.activate
    def test_connection_error_is_reported_as_temporary(self):
        responses.add(responses.GET, ASSERTION_URL, body=requests.ConnectionError('refused'))
        success, message, actions = fetch_http_node({}, add_task(FETCH_HTTP_NODE, url=ASSERTION_URL))
        self.assertFalse(success)
        self.assertTrue(message.startswith(TEMPORARY_FETCH_ERROR), message)

    @responses.activate
    def test_fetch_not_allowed_is_not_converted(self):
        responses.add(responses.GET, ASSERTION_URL, body=FetchNotAllowedError('not allowed'))
        with self.assertRaises(FetchNotAllowedError):
            fetch_http_node({}, add_task(FETCH_HTTP_NODE, url=ASSERTION_URL))


class FetchTimeoutTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(FETCH_TIMEOUT_ENV, None)

    def tearDown(self):
        os.environ.pop(FETCH_TIMEOUT_ENV, None)
        if self._saved is not None:
            os.environ[FETCH_TIMEOUT_ENV] = self._saved

    def test_default_timeout(self):
        self.assertEqual(get_fetch_timeout(), 10.0)

    def test_timeout_from_env(self):
        os.environ[FETCH_TIMEOUT_ENV] = '3.5'
        self.assertEqual(get_fetch_timeout(), 3.5)

    def test_invalid_timeout_falls_back_to_default(self):
        for value in ('abc', '0', '-1'):
            os.environ[FETCH_TIMEOUT_ENV] = value
            self.assertEqual(get_fetch_timeout(), 10.0, value)

    def test_adapter_applies_timeout_when_none_given(self):
        os.environ[FETCH_TIMEOUT_ENV] = '7'
        seen = {}

        def fake_send(self, request, *args, **kwargs):
            seen['timeout'] = kwargs.get('timeout')
            raise requests.ConnectionError('stop')

        session = apply_fetch_policy(requests.Session())
        with mock.patch('requests.adapters.HTTPAdapter.send', fake_send):
            with self.assertRaises(requests.ConnectionError):
                session.get('https://lms.example.org/x')
        self.assertEqual(seen['timeout'], 7.0)
