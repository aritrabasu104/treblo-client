"""No real accounts are needed for the publication and error privacy checks."""
import ast
from pathlib import Path
import re
import sys
import traceback
import unittest
from urllib.error import URLError

from treblo_client.transport import HttpTransport, OutcomeUnknown, TransportUnavailable
from treblo_client.protocol import Request

ROOT=Path(__file__).resolve().parents[1]
PACKAGE='treblo_client'


def private_patterns(text):
    patterns=(r'/(?:Users|home)/[A-Za-z][^/\s"\x27]+/',
              r'[A-Z]:\\Users\\[^\\\s]+\\',
              r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
              r'gh[pousr]_[A-Za-z0-9]{20,}',
              r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')
    return any(re.search(pattern,text) for pattern in patterns)


class PublicationPrivacyTests(unittest.TestCase):
    def test_public_files_have_no_machine_paths_or_credential_blobs(self):
        files=list((ROOT/'src'/PACKAGE).glob('*.py'))
        files+=list((ROOT/'docs').glob('*.md'))+list((ROOT/'examples').glob('*.py'))
        files += [path for path in (ROOT/'README.md',ROOT/'pyproject.toml',ROOT/'SECURITY.md') if path.exists()]
        for path in files:
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertFalse(private_patterns(path.read_text()))

    def test_package_imports_only_itself_and_declared_public_dependencies(self):
        allowed=set(sys.stdlib_module_names)|{PACKAGE,'playwright'}
        for path in (ROOT/'src'/PACKAGE).glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.Import):
                    for name in node.names:self.assertIn(name.name.split('.')[0],allowed,path.name)
                elif isinstance(node,ast.ImportFrom) and node.level==0:
                    self.assertIn(node.module.split('.')[0],allowed,path.name)

    def test_no_literal_account_secrets_in_package_configuration(self):
        sensitive={'access_token','refresh_token','client_secret','password','code_verifier','id_token','authorization','cookie','apikey'}
        for path in (ROOT/'src'/PACKAGE).glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                pairs=[]
                if isinstance(node,ast.Dict):pairs=list(zip(node.keys,node.values))
                if isinstance(node,ast.Assign):
                    pairs=[(ast.Constant(target.id),node.value) for target in node.targets if isinstance(target,ast.Name)]
                for key,value in pairs:
                    if (isinstance(key,ast.Constant) and isinstance(key.value,str)
                        and key.value.lower() in sensitive and isinstance(value,ast.Constant)
                        and isinstance(value.value,str)):
                        self.assertFalse(value.value,path.name)


class ErrorPrivacyTests(unittest.TestCase):
    def test_transport_failure_tracebacks_exclude_sensitive_urls(self):
        marker='synthetic-private-query-value'
        class Broken:
            calls=0
            def open(self,*args,**kwargs):
                self.calls+=1
                raise URLError('https://example.invalid/?access_token='+marker)
        for mutation in (False,True):
            transport=HttpTransport();opener=Broken();transport.opener=opener
            with self.subTest(mutation=mutation):
                try:transport.execute(Request('POST' if mutation else 'GET','https://treblo.com/api/v1/users/me'))
                except (OutcomeUnknown,TransportUnavailable) as error:
                    rendered=''.join(traceback.format_exception(error))
                    self.assertNotIn(marker,rendered)
                    self.assertTrue(error.__suppress_context__)
                    self.assertEqual(opener.calls,1)
                else:self.fail('Expected a typed, sanitized transport failure')

    def test_account_and_request_representations_exclude_private_fields(self):
        import time
        from treblo_client import Session
        private='synthetic-private-account-marker'
        session=Session(private,private,time.time()+3600,private,private,private)
        request=Request('GET','https://treblo.com/'+private)
        self.assertNotIn(private,repr(session))
        self.assertNotIn(private,repr(request))
