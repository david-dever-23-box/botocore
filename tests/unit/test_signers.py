# Copyright 2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

import mock
import datetime
import json

import botocore
import botocore.auth
from botocore.compat import six, urlparse, parse_qs

from botocore.credentials import Credentials
from botocore.exceptions import NoRegionError, UnknownSignatureVersionError
from botocore.exceptions import UnknownClientMethodError, ParamValidationError
from botocore.exceptions import UnsupportedSignatureVersionError
from botocore.signers import RequestSigner, S3PostPresigner, CloudFrontSigner

from tests import unittest


class BaseSignerTest(unittest.TestCase):
    def setUp(self):
        self.credentials = Credentials('key', 'secret')
        self.emitter = mock.Mock()
        self.emitter.emit_until_response.return_value = (None, None)
        self.signer = RequestSigner(
            'service_name', 'region_name', 'signing_name',
            'v4', self.credentials, self.emitter)


class TestSigner(BaseSignerTest):

    def test_region_name(self):
        self.assertEqual(self.signer.region_name, 'region_name')

    def test_signature_version(self):
        self.assertEqual(self.signer.signature_version, 'v4')

    def test_signing_name(self):
        self.assertEqual(self.signer.signing_name, 'signing_name')

    def test_region_required_for_sigv4(self):
        self.signer = RequestSigner(
            'service_name', None, 'signing_name', 'v4', self.credentials,
            self.emitter)

        with self.assertRaises(NoRegionError):
            self.signer.sign('operation_name', mock.Mock())

    def test_get_auth(self):
        auth_cls = mock.Mock()
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'v4': auth_cls}):
            auth = self.signer.get_auth('service_name', 'region_name')

            self.assertEqual(auth, auth_cls.return_value)
            auth_cls.assert_called_with(
                credentials=self.credentials, service_name='service_name',
                region_name='region_name')

    def test_get_auth_cached(self):
        def side_effect(*args, **kwargs):
            return mock.Mock()
        auth_cls = mock.Mock(side_effect=side_effect)
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'v4': auth_cls}):
            auth1 = self.signer.get_auth('service_name', 'region_name')
            auth2 = self.signer.get_auth('service_name', 'region_name')

        self.assertEqual(auth1, auth2)

    def test_get_auth_cached_expires(self):
        def side_effect(*args, **kwargs):
            return mock.Mock()
        auth_cls = mock.Mock(side_effect=side_effect)
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'v4': auth_cls}):
            auth1 = self.signer.get_auth('service_name', 'region_name',
                                         expires=60)
            auth2 = self.signer.get_auth('service_name', 'region_name',
                                         expires=90)

        self.assertNotEqual(auth1, auth2)

    def test_get_auth_signature_override(self):
        auth_cls = mock.Mock()
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'v4-custom': auth_cls}):
            auth = self.signer.get_auth(
                'service_name', 'region_name', signature_version='v4-custom')

            self.assertEqual(auth, auth_cls.return_value)
            auth_cls.assert_called_with(
                credentials=self.credentials, service_name='service_name',
                region_name='region_name')

    def test_get_auth_bad_override(self):
        with self.assertRaises(UnknownSignatureVersionError):
            self.signer.get_auth('service_name', 'region_name',
                                 signature_version='bad')

    def test_emits_choose_signer(self):
        request = mock.Mock()

        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'v4': mock.Mock()}):
            self.signer.sign('operation_name', request)

        self.emitter.emit_until_response.assert_called_with(
            'choose-signer.service_name.operation_name',
            signing_name='signing_name', region_name='region_name',
            signature_version='v4')

    def test_choose_signer_override(self):
        request = mock.Mock()
        auth = mock.Mock()
        auth.REQUIRES_REGION = False
        self.emitter.emit_until_response.return_value = (None, 'custom')

        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'custom': auth}):
            self.signer.sign('operation_name', request)

        auth.assert_called_with(credentials=self.credentials)
        auth.return_value.add_auth.assert_called_with(request=request)

    def test_emits_before_sign(self):
        request = mock.Mock()

        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'v4': mock.Mock()}):
            self.signer.sign('operation_name', request)

        self.emitter.emit.assert_called_with(
            'before-sign.service_name.operation_name',
            request=mock.ANY, signing_name='signing_name',
            region_name='region_name', signature_version='v4',
            request_signer=self.signer)

    def test_disable_signing(self):
        # Returning botocore.UNSIGNED from choose-signer disables signing!
        request = mock.Mock()
        auth = mock.Mock()
        self.emitter.emit_until_response.return_value = (None,
                                                         botocore.UNSIGNED)

        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'v4': auth}):
            self.signer.sign('operation_name', request)

        auth.assert_not_called()

    def test_generate_presigned_url(self):
        auth = mock.Mock()
        auth.REQUIRES_REGION = True

        request_dict = {
            'headers': {},
            'url': 'https://foo.com',
            'body': b'',
            'url_path': '/',
            'method': 'GET'
        }
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'v4-query': auth}):
            presigned_url = self.signer.generate_presigned_url(request_dict)
        auth.assert_called_with(
            credentials=self.credentials, region_name='region_name',
            service_name='signing_name', expires=3600)
        self.assertEqual(presigned_url, 'https://foo.com')

    def test_generate_presigned_url_with_region_override(self):
        auth = mock.Mock()
        auth.REQUIRES_REGION = True

        request_dict = {
            'headers': {},
            'url': 'https://foo.com',
            'body': b'',
            'url_path': '/',
            'method': 'GET'
        }
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'v4-query': auth}):
            presigned_url = self.signer.generate_presigned_url(
                request_dict, region_name='us-west-2')
        auth.assert_called_with(
            credentials=self.credentials, region_name='us-west-2',
            service_name='signing_name', expires=3600)
        self.assertEqual(presigned_url, 'https://foo.com')

    def test_generate_presigned_url_with_exipres_in(self):
        auth = mock.Mock()
        auth.REQUIRES_REGION = True

        request_dict = {
            'headers': {},
            'url': 'https://foo.com',
            'body': b'',
            'url_path': '/',
            'method': 'GET'
        }
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'v4-query': auth}):
            presigned_url = self.signer.generate_presigned_url(
                request_dict, expires_in=900)
        auth.assert_called_with(
            credentials=self.credentials, region_name='region_name',
            expires=900, service_name='signing_name')
        self.assertEqual(presigned_url, 'https://foo.com')

    def test_generate_presigned_url_fixes_s3_host(self):
        self.signer = RequestSigner(
            'service_name', 'region_name', 'signing_name',
            's3', self.credentials, self.emitter)

        auth = mock.Mock()
        auth.REQUIRES_REGION = True

        request_dict = {
            'headers': {},
            'url': 'https://s3.amazonaws.com/mybucket/myobject',
            'body': b'',
            'url_path': '/',
            'method': 'GET'
        }
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'s3-query': auth}):
            presigned_url = self.signer.generate_presigned_url(
                request_dict, expires_in=900)
        auth.assert_called_with(
            credentials=self.credentials, region_name='region_name',
            expires=900, service_name='signing_name')
        self.assertEqual(presigned_url,
                         'https://mybucket.s3.amazonaws.com/myobject')

    def test_presigned_url_throws_unsupported_signature_error(self):
        self.signer = RequestSigner(
            'service_name', 'region_name', 'signing_name',
            'foo', self.credentials, self.emitter)
        with self.assertRaises(UnsupportedSignatureVersionError):
            self.signer.generate_presigned_url({})


class TestCloudfrontSigner(unittest.TestCase):
    def setUp(self):
        self.signer = CloudFrontSigner("MY_KEY_ID", lambda message: b'signed')
        # It helps but the long string diff will still be slightly different on
        # Python 2.6/2.7/3.x. We won't soly rely on that anyway, so it's fine.
        self.maxDiff = None

    def test_build_canned_policy(self):
        policy = self.signer.build_policy('foo', datetime.datetime(2016, 1, 1))
        expected = (
            '{"Statement":[{"Resource":"foo",'
            '"Condition":{"DateLessThan":{"AWS:EpochTime":1451606400}}}]}')
        self.assertEqual(json.loads(policy), json.loads(expected))
        self.assertEqual(policy, expected)  # This is to ensure the right order

    def test_build_custom_policy(self):
        policy = self.signer.build_policy(
            'foo', datetime.datetime(2016, 1, 1),
            date_greater_than=datetime.datetime(2015, 12, 1),
            ip_address='12.34.56.78/9')
        expected = {
            "Statement": [{
                "Resource":"foo",
                "Condition":{
                    "DateGreaterThan":{"AWS:EpochTime":1448928000},
                    "DateLessThan":{"AWS:EpochTime":1451606400},
                    "IpAddress":{"AWS:SourceIp":"12.34.56.78/9"}
                },
            }]
        }
        self.assertEqual(json.loads(policy), expected)

    def _urlparse(self, url):
        if isinstance(url, six.binary_type):
            # Not really necessary, but it helps to reduce noise on Python 2.x
            url = url.decode('utf8')
        return dict(urlparse(url)._asdict())  # Needs an unordered dict here

    def assertEqualUrl(self, url1, url2):
        # We compare long urls by their dictionary parts
        parts1 = self._urlparse(url1)
        parts2 = self._urlparse(url2)
        self.assertEqual(
            parse_qs(parts1.pop('query')), parse_qs(parts2.pop('query')))
        self.assertEqual(parts1, parts2)

    def test_generate_presign_url_with_expire_time(self):
        signed_url = self.signer.generate_presigned_url(
            'http://test.com/foo.txt',
            date_less_than=datetime.datetime(2016, 1, 1))
        expected = (
            'http://test.com/foo.txt?Expires=1451606400&Signature=c2lnbmVk'
            '&Key-Pair-Id=MY_KEY_ID')
        self.assertEqualUrl(signed_url, expected)

    def test_generate_presign_url_with_custom_policy(self):
        policy = self.signer.build_policy(
            'foo', datetime.datetime(2016, 1, 1),
            date_greater_than=datetime.datetime(2015, 12, 1),
            ip_address='12.34.56.78/9')
        signed_url = self.signer.generate_presigned_url(
            'http://test.com/index.html?foo=bar', policy=policy)
        expected = (
            'http://test.com/index.html?foo=bar'
            '&Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiZm9vIiwiQ29uZ'
                'Gl0aW9uIjp7IkRhdGVMZXNzVGhhbiI6eyJBV1M6RXBvY2hUaW1lIj'
                'oxNDUxNjA2NDAwfSwiSXBBZGRyZXNzIjp7IkFXUzpTb3VyY2VJcCI'
                '6IjEyLjM0LjU2Ljc4LzkifSwiRGF0ZUdyZWF0ZXJUaGFuIjp7IkFX'
                'UzpFcG9jaFRpbWUiOjE0NDg5MjgwMDB9fX1dfQ__'
            '&Signature=c2lnbmVk&Key-Pair-Id=MY_KEY_ID')
        self.assertEqualUrl(signed_url, expected)


class TestS3PostPresigner(BaseSignerTest):
    def setUp(self):
        super(TestS3PostPresigner, self).setUp()
        self.request_signer = RequestSigner(
            'service_name', 'region_name', 'signing_name',
            's3v4', self.credentials, self.emitter)
        self.signer = S3PostPresigner(self.request_signer)
        self.request_dict = {
            'headers': {},
            'url': 'https://s3.amazonaws.com/mybucket',
            'body': b'',
            'url_path': '/',
            'method': 'POST'
        }
        self.auth = mock.Mock()
        self.auth.REQUIRES_REGION = True
        self.add_auth = mock.Mock()
        self.auth.return_value.add_auth = self.add_auth

        self.datetime_patch = mock.patch('botocore.signers.datetime')
        self.datetime_mock = self.datetime_patch.start()
        self.fixed_date = datetime.datetime(2014, 3, 10, 17, 2, 55, 0)
        self.fixed_delta = datetime.timedelta(seconds=3600)
        self.datetime_mock.datetime.utcnow.return_value = self.fixed_date
        self.datetime_mock.timedelta.return_value = self.fixed_delta

    def tearDown(self):
        super(TestS3PostPresigner, self).tearDown()
        self.datetime_patch.stop()

    def test_generate_presigned_post(self):
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'s3v4-presign-post': self.auth}):
            post_form_args = self.signer.generate_presigned_post(
                self.request_dict)
        self.auth.assert_called_with(
            credentials=self.credentials, region_name='region_name',
            service_name='signing_name')
        self.add_auth.assert_called_once()
        ref_request = self.add_auth.call_args[0][0]
        ref_policy = ref_request.context['s3-presign-post-policy']
        self.assertEqual(ref_policy['expiration'], '2014-03-10T18:02:55Z')
        self.assertEqual(ref_policy['conditions'], [])

        self.assertEqual(post_form_args['url'],
                         'https://s3.amazonaws.com/mybucket')
        self.assertEqual(post_form_args['fields'], {})

    def test_generate_presigned_post_with_conditions(self):
        conditions = [
            {'bucket': 'mybucket'},
            ['starts-with', '$key', 'bar']
        ]
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'s3v4-presign-post': self.auth}):
            self.signer.generate_presigned_post(
                self.request_dict, conditions=conditions)
        self.auth.assert_called_with(
            credentials=self.credentials, region_name='region_name',
            service_name='signing_name')
        self.add_auth.assert_called_once()
        ref_request = self.add_auth.call_args[0][0]
        ref_policy = ref_request.context['s3-presign-post-policy']
        self.assertEqual(ref_policy['conditions'], conditions)

    def test_generate_presigned_post_with_region_override(self):
        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'s3v4-presign-post': self.auth}):
            self.signer.generate_presigned_post(
                self.request_dict, region_name='foo')
        self.auth.assert_called_with(
            credentials=self.credentials, region_name='foo',
            service_name='signing_name')

    def test_generate_presigned_post_fixes_s3_host(self):
        self.request_signer = RequestSigner(
            'service_name', 'region_name', 'signing_name',
            's3', self.credentials, self.emitter)
        self.signer = S3PostPresigner(self.request_signer)

        with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS,
                             {'s3-presign-post': self.auth}):
            post_form_args = self.signer.generate_presigned_post(
                self.request_dict)
        self.auth.assert_called_with(
            credentials=self.credentials, region_name='region_name',
            service_name='signing_name')
        self.assertEqual(post_form_args['url'],
                         'https://mybucket.s3.amazonaws.com/')

    def test_presigned_post_throws_unsupported_signature_error(self):
        self.request_signer = RequestSigner(
            'service_name', 'region_name', 'signing_name',
            'foo', self.credentials, self.emitter)
        self.signer = S3PostPresigner(self.request_signer)
        with self.assertRaises(UnsupportedSignatureVersionError):
            self.signer.generate_presigned_post({})


class TestGenerateUrl(unittest.TestCase):
    def setUp(self):
        self.session = botocore.session.get_session()
        self.client = self.session.create_client('s3', region_name='us-east-1')
        self.bucket = 'mybucket'
        self.key = 'mykey'
        self.client_kwargs = {'Bucket': self.bucket, 'Key': self.key}
        self.generate_url_patch = mock.patch(
            'botocore.signers.RequestSigner.generate_presigned_url')
        self.generate_url_mock = self.generate_url_patch.start()

    def tearDown(self):
        self.generate_url_patch.stop()

    def test_generate_presigned_url(self):
        self.client.generate_presigned_url(
            'get_object', Params={'Bucket': self.bucket, 'Key': self.key})

        ref_request_dict = {
            'body': b'',
            'url': u'https://s3.amazonaws.com/mybucket/mykey',
            'headers': {},
            'query_string': {},
            'url_path': u'/mybucket/mykey',
            'method': u'GET'}
        self.generate_url_mock.assert_called_with(
            request_dict=ref_request_dict, expires_in=3600)

    def test_generate_presigned_url_with_query_string(self):
        disposition = 'attachment; filename="download.jpg"'
        self.client.generate_presigned_url(
            'get_object', Params={
                'Bucket': self.bucket,
                'Key': self.key,
                'ResponseContentDisposition': disposition})
        ref_request_dict = {
            'body': b'',
            'url': (u'https://s3.amazonaws.com/mybucket/mykey'
                    '?response-content-disposition='
                    'attachment%3B%20filename%3D%22download.jpg%22'),
            'headers': {},
            'query_string': {u'response-content-disposition': disposition},
            'url_path': u'/mybucket/mykey',
            'method': u'GET'}
        self.generate_url_mock.assert_called_with(
            request_dict=ref_request_dict, expires_in=3600)

    def test_generate_presigned_url_unknown_method_name(self):
        with self.assertRaises(UnknownClientMethodError):
            self.client.generate_presigned_url('getobject')

    def test_generate_presigned_url_missing_required_params(self):
        with self.assertRaises(ParamValidationError):
            self.client.generate_presigned_url('get_object')

    def test_generate_presigned_url_expires(self):
        self.client.generate_presigned_url(
            'get_object', Params={'Bucket': self.bucket, 'Key': self.key},
            ExpiresIn=20)
        ref_request_dict = {
            'body': b'',
            'url': u'https://s3.amazonaws.com/mybucket/mykey',
            'headers': {},
            'query_string': {},
            'url_path': u'/mybucket/mykey',
            'method': u'GET'}
        self.generate_url_mock.assert_called_with(
            request_dict=ref_request_dict, expires_in=20)

    def test_generate_presigned_url_override_http_method(self):
        self.client.generate_presigned_url(
            'get_object', Params={'Bucket': self.bucket, 'Key': self.key},
            HttpMethod='PUT')
        ref_request_dict = {
            'body': b'',
            'url': u'https://s3.amazonaws.com/mybucket/mykey',
            'headers': {},
            'query_string': {},
            'url_path': u'/mybucket/mykey',
            'method': u'PUT'}
        self.generate_url_mock.assert_called_with(
            request_dict=ref_request_dict, expires_in=3600)


class TestGeneratePresignedPost(unittest.TestCase):
    def setUp(self):
        self.session = botocore.session.get_session()
        self.client = self.session.create_client('s3', region_name='us-east-1')
        self.bucket = 'mybucket'
        self.key = 'mykey'
        self.presign_post_patch = mock.patch(
            'botocore.signers.S3PostPresigner.generate_presigned_post')
        self.presign_post_mock = self.presign_post_patch.start()

    def tearDown(self):
        self.presign_post_patch.stop()

    def test_generate_presigned_post(self):
        self.client.generate_presigned_post(self.bucket, self.key)

        _, post_kwargs = self.presign_post_mock.call_args
        request_dict = post_kwargs['request_dict']
        fields = post_kwargs['fields']
        conditions = post_kwargs['conditions']
        self.assertEqual(
            request_dict['url'], 'https://s3.amazonaws.com/mybucket')
        self.assertEqual(post_kwargs['expires_in'], 3600)
        self.assertEqual(
            conditions,
            [{'bucket': 'mybucket'}, {'key': 'mykey'}])
        self.assertEqual(
            fields,
            {'key': 'mykey'})

    def test_generate_presigned_post_with_filename(self):
        self.key = 'myprefix/${filename}'
        self.client.generate_presigned_post(self.bucket, self.key)

        _, post_kwargs = self.presign_post_mock.call_args
        request_dict = post_kwargs['request_dict']
        fields = post_kwargs['fields']
        conditions = post_kwargs['conditions']
        self.assertEqual(
            request_dict['url'], 'https://s3.amazonaws.com/mybucket')
        self.assertEqual(post_kwargs['expires_in'], 3600)
        self.assertEqual(
            conditions,
            [{'bucket': 'mybucket'}, ['starts-with', '$key', 'myprefix/']])
        self.assertEqual(
            fields,
            {'key': 'myprefix/${filename}'})

    def test_generate_presigned_post_expires(self):
        self.client.generate_presigned_post(
            self.bucket, self.key, ExpiresIn=50)
        _, post_kwargs = self.presign_post_mock.call_args
        request_dict = post_kwargs['request_dict']
        fields = post_kwargs['fields']
        conditions = post_kwargs['conditions']
        self.assertEqual(
            request_dict['url'], 'https://s3.amazonaws.com/mybucket')
        self.assertEqual(post_kwargs['expires_in'], 50)
        self.assertEqual(
            conditions,
            [{'bucket': 'mybucket'}, {'key': 'mykey'}])
        self.assertEqual(
            fields,
            {'key': 'mykey'})

    def test_generate_presigned_post_with_prefilled(self):
        conditions = [{'acl': 'public-read'}]
        fields = {'acl': 'public-read'}

        self.client.generate_presigned_post(
            self.bucket, self.key, Fields=fields, Conditions=conditions)

        _, post_kwargs = self.presign_post_mock.call_args
        request_dict = post_kwargs['request_dict']
        fields = post_kwargs['fields']
        conditions = post_kwargs['conditions']
        self.assertEqual(
            request_dict['url'], 'https://s3.amazonaws.com/mybucket')
        self.assertEqual(
            conditions,
            [{'acl': 'public-read'}, {'bucket': 'mybucket'}, {'key': 'mykey'}])
        self.assertEqual(fields['acl'], 'public-read')
        self.assertEqual(
            fields, {'key': 'mykey', 'acl': 'public-read'})

    def test_generate_presigned_post_non_s3_client(self):
        self.client = self.session.create_client('ec2', 'us-west-2')
        with self.assertRaises(AttributeError):
            self.client.generate_presigned_post()
