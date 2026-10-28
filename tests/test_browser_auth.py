import unittest
from unittest.mock import Mock

from treblo_client import AuthClient
from treblo_client.browser import _PlaywrightTab


class BrowserAuthTests(unittest.TestCase):
    def test_owned_auth_tab_closes_on_success_and_all_failures(self):
        for stage in ['success', 'timeout', 'callback', 'exchange', 'identity']:
            with self.subTest(stage=stage):
                auth = AuthClient(expected_email='owner@example.test', publishable_key='synthetic-key')
                tab = Mock()
                tab.wait_for_callback.return_value = 'https://treblo.com/robots.txt?code=synthetic-code'
                auth.exchange_callback = Mock(return_value=object())
                auth.verify = Mock()
                if stage == 'timeout': tab.wait_for_callback.side_effect = TimeoutError()
                if stage == 'callback': auth.exchange_callback.side_effect = ValueError('callback')
                if stage == 'exchange': auth.exchange_callback.side_effect = RuntimeError('exchange')
                if stage == 'identity': auth.verify.side_effect = ValueError('account mismatch')
                browser = Mock()
                browser.open.return_value = tab
                if stage == 'success':
                    self.assertIs(auth.login_with_google(browser), auth.exchange_callback.return_value)
                else:
                    with self.assertRaises(Exception): auth.login_with_google(browser)
                tab.close.assert_called_once_with()
                browser.close.assert_not_called()

    def test_cancellation_still_closes_owned_tab(self):
        auth = AuthClient(expected_email='owner@example.test', publishable_key='synthetic-key')
        browser = Mock(); browser.open.return_value.wait_for_callback.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): auth.login_with_google(browser)
        browser.open.return_value.close.assert_called_once()

    def test_existing_browser_is_never_closed_but_own_tab_is(self):
        for owned in [True, False]:
            page, browser, runtime = Mock(), Mock(), Mock()
            page.is_closed.return_value = False
            tab = _PlaywrightTab(page, browser, runtime, owned)
            tab.close(); tab.close()
            page.close.assert_called_once()
            runtime.stop.assert_called_once()
            self.assertEqual(browser.close.call_count, int(owned))
