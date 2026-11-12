import json
import os
import unittest

try:
    from unittest import mock
except ImportError:
    import mock

import requests
import responses

from openbadges.verifier import verify
from openbadges.verifier.fetch_policy import (ALLOWED_FETCH_HOSTS_ENV, FetchNotAllowedError,
                                              apply_fetch_policy, check_url_allowed, get_allowed_hosts)
from openbadges.verifier.utils import CachableDocumentLoader

try:
    from .testfiles.test_components import test_components
except (ImportError, SystemError):
    from tests.testfiles.test_components import test_components


ALLOWED = 'lms.example.org, LMS2.example.org ,,'


def with_allowed_hosts(value):
    return mock.patch.dict(os.environ, {ALLOWED_FETCH_HOSTS_ENV: value})


class AllowedHostsParsingTests(unittest.TestCase):
    def test_unset_allows_everything(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(get_allowed_hosts())
            check_url_allowed('http://anything.example.net:8080/path')

    def test_empty_allows_everything(self):
        with with_allowed_hosts(' , '):
            self.assertIsNone(get_allowed_hosts())
            check_url_allowed('http://anything.example.net:8080/path')

    def test_list_is_trimmed_and_lowercased(self):
        with with_allowed_hosts(ALLOWED):
            self.assertEqual(get_allowed_hosts(), frozenset(['lms.example.org', 'lms2.example.org']))


class CheckUrlTests(unittest.TestCase):
    def assertAllowed(self, url):
        check_url_allowed(url)

    def assertBlocked(self, url):
        with self.assertRaises(FetchNotAllowedError):
            check_url_allowed(url)

    def test_allowed_urls(self):
        with with_allowed_hosts(ALLOWED):
            self.assertAllowed('https://lms.example.org/badges/assertion.php?b=1')
            self.assertAllowed('https://LMS.Example.org/badges/badge_json.php?id=1')
            self.assertAllowed('https://lms2.example.org:443/x')

    def test_blocked_urls(self):
        with with_allowed_hosts(ALLOWED):
            self.assertBlocked('http://lms.example.org/x')  # scheme must be https
            self.assertBlocked('https://lms.example.org:8443/x')  # port must be 443
            self.assertBlocked('https://other.example.org/x')  # host not listed
            self.assertBlocked('https://evil.lms.example.org/x')  # no subdomain match
            self.assertBlocked('https://lms.example.org.attacker.example/x')  # no suffix match
            self.assertBlocked('https://lms.example.org@attacker.example/x')  # userinfo trick
            self.assertBlocked('https://10.0.0.1/x')  # raw IP not listed
            self.assertBlocked('https://lms.example.org:notaport/x')  # unparseable port
            self.assertBlocked('ftp://lms.example.org/x')


class SessionPolicyTests(unittest.TestCase):
    @responses.activate
    def test_allowed_host_is_fetched(self):
        responses.add(responses.GET, 'https://lms.example.org/a', json={'ok': True})
        session = apply_fetch_policy(requests.Session())
        with with_allowed_hosts(ALLOWED):
            self.assertEqual(session.get('https://lms.example.org/a').json(), {'ok': True})
        self.assertEqual(len(responses.calls), 1)

    @responses.activate
    def test_blocked_host_is_not_requested(self):
        responses.add(responses.GET, 'https://other.example.org/a', json={'ok': True})
        session = apply_fetch_policy(requests.Session())
        with with_allowed_hosts(ALLOWED):
            with self.assertRaises(FetchNotAllowedError):
                session.get('https://other.example.org/a')
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_redirect_to_blocked_host_is_not_followed(self):
        responses.add(responses.GET, 'https://lms.example.org/a', status=302,
                      headers={'Location': 'https://other.example.org/b'})
        responses.add(responses.GET, 'https://other.example.org/b', json={'secret': True})
        session = apply_fetch_policy(requests.Session())
        with with_allowed_hosts(ALLOWED):
            with self.assertRaises(FetchNotAllowedError):
                session.get('https://lms.example.org/a')
        self.assertEqual([c.request.url for c in responses.calls], ['https://lms.example.org/a'])

    @responses.activate
    def test_document_loader_applies_policy(self):
        responses.add(responses.GET, 'https://other.example.org/context.json', json={'@context': {}})
        loader = CachableDocumentLoader(use_cache=False)
        with with_allowed_hosts(ALLOWED):
            with self.assertRaises(Exception):
                loader('https://other.example.org/context.json')
        self.assertEqual(len(responses.calls), 0)


class VerifyWithPolicyTests(unittest.TestCase):
    @responses.activate
    def test_hosted_assertion_on_blocked_host_is_not_fetched(self):
        assertion = json.loads(test_components['2_0_basic_assertion'])
        with with_allowed_hosts(ALLOWED):
            results = verify(json.dumps(assertion))

        self.assertFalse(results['report']['valid'])
        self.assertEqual(len(responses.calls), 0)
        error_results = ' '.join(
            str(m.get('result')) for m in results['report']['messages'] if m['messageLevel'] == 'ERROR')
        self.assertIn('FetchNotAllowedError', error_results)
