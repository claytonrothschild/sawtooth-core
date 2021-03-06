# Copyright 2016, 2017 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ------------------------------------------------------------------------------

import struct
import math
import logging
import threading
import datetime
import hashlib
import base64
import time

from cryptography.hazmat import backends
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature

import sawtooth_signing as signing

from sawtooth_validator.journal.block_wrapper import NULL_BLOCK_IDENTIFIER

from sawtooth_poet_common import sgx_structs

from sawtooth_poet_simulator.poet_enclave_simulator.utils import json2dict
from sawtooth_poet_simulator.poet_enclave_simulator.utils import dict2json

from sawtooth_poet_simulator.poet_enclave_simulator.enclave_signup_info \
    import EnclaveSignupInfo
from sawtooth_poet_simulator.poet_enclave_simulator.enclave_wait_timer \
    import EnclaveWaitTimer
from sawtooth_poet_simulator.poet_enclave_simulator.enclave_wait_certificate \
    import EnclaveWaitCertificate

LOGGER = logging.getLogger(__name__)

TIMER_TIMEOUT_PERIOD = 30.0


class _PoetEnclaveSimulator(object):
    # A lock to protect threaded access
    _lock = threading.Lock()

    # The private key we generate to sign the certificate ID when creating
    # the random wait timeout value
    _seal_private_key = signing.generate_privkey()
    _seal_public_key = signing.generate_pubkey(_seal_private_key)

    # The basename and enclave measurement values we will put into and verify
    # are in the enclave quote in the attestation verification report.
    __VALID_BASENAME__ = \
        bytes.fromhex(
            'b785c58b77152cbe7fd55ee3851c4990'
            '00000000000000000000000000000000')
    __VALID_ENCLAVE_MEASUREMENT__ = \
        bytes.fromhex(
            'c99f21955e38dbb03d2ca838d3af6e43'
            'ef438926ed02db4cc729380c8c7a174e')

    # We use the report private key PEM to create the private key used to
    # sign attestation verification reports.  On the flip side, the report
    # public key PEM is used to create the public key used to verify the
    # signature on the attestation verification reports.
    __REPORT_PRIVATE_KEY_PEM__ = \
        '-----BEGIN PRIVATE KEY-----\n' \
        'MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCsy/NmLwZP6Uj0\n' \
        'p5mIiefgK8VOK7KJ34g3h0/X6aFOd/Ff4j+e23wtQpkxsjVHWLM5SjElGhfpVDhL\n' \
        '1WAMsQI9bpCWR4sjV6p7gOJhv34nkA2Grj5eSHCAJRQXCl+pJ9dYIeKaNoaxkdtq\n' \
        '+Xme//ohtkkv/ZjMTfsjMl0RLXokJ+YhSuTpNSovRaCtZfLB5MihVJuV3Qzb2ROh\n' \
        'KQxcuyPy9tBtOIrBWJaFiXOLRxAijs+ICyzrqUBbRfoAztkljIBx9KNItHiC4zPv\n' \
        'o6DxpGSO2yMQSSrs13PkfyGWVZSgenEYOouEz07X+H5B29PPuW5mCl4nkoH3a9gv\n' \
        'rI6VLEx9AgMBAAECggEAImfFge4RCq4/eX85gcc7pRXyBjuLJAqe+7d0fWAmXxJg\n' \
        'vB+3XTEEi5p8GDoMg7U0kk6kdGe6pRnAz9CffEduU78FCPcbzCCzcD3cVWwkeUok\n' \
        'd1GQV4OC6vD3DBNjsrGdHg45KU18CjUphCZCQhdjvXynG+gZmWxZecuYXkg4zqPT\n' \
        'LwOkcdWBPhJ9CbjtiYOtKDZbhcbdfnb2fkxmvnAoz1OWNfVFXh+x7651FrmL2Pga\n' \
        'xGz5XoxFYYT6DWW1fL6GNuVrd97wkcYUcjazMgunuUMC+6XFxqK+BoqnxeaxnsSt\n' \
        'G2r0sdVaCyK1sU41ftbEQsc5oYeQ3v5frGZL+BgrYQKBgQDgZnjqnVI/B+9iarx1\n' \
        'MjAFyhurcKvFvlBtGKUg9Q62V6wI4VZvPnzA2zEaR1J0cZPB1lCcMsFACpuQF2Mr\n' \
        '3VDyJbnpSG9q05POBtfLjGQdXKtGb8cfXY2SwjzLH/tvxHm3SP+RxvLICQcLX2/y\n' \
        'GTJ+mY9C6Hs6jIVLOnMWkRWamQKBgQDFITE3Qs3Y0ZwkKfGQMKuqJLRw29Tyzw0n\n' \
        'XKaVmO/pEzYcXZMPBrFhGvdmNcJLo2fcsmGZnmit8RP4ChwHUlD11dH1Ffqw9FWc\n' \
        '387i0chlE5FhQPirSM8sWFVmjt2sxC4qFWJoAD/COQtKHgEaVKVc4sH/yRostL1C\n' \
        'r+7aWuqzhQKBgQDcuC5LJr8VPGrbtPz1kY3mw+r/cG2krRNSm6Egj6oO9KFEgtCP\n' \
        'zzjKQU9E985EtsqNKI5VdR7cLRLiYf6r0J6j7zO0IAlnXADP768miUqYDuRw/dUw\n' \
        'JsbwCZneefDI+Mp325d1/egjla2WJCNqUBp4p/Zf62f6KOmbGzzEf6RuUQKBgG2y\n' \
        'E8YRiaTOt5m0MXUwcEZk2Hg5DF31c/dkalqy2UYU57aPJ8djzQ8hR2x8G9ulWaWJ\n' \
        'KiCm8s9gaOFNFt3II785NfWxPmh7/qwmKuUzIdWFNxAsbHQ8NvURTqyccaSzIpFO\n' \
        'hw0inlhBEBQ1cB2r3r06fgQNb2BTT0Itzrd5gkNVAoGBAJcMgeKdBMukT8dKxb4R\n' \
        '1PgQtFlR3COu2+B00pDyUpROFhHYLw/KlUv5TKrH1k3+E0KM+winVUIcZHlmFyuy\n' \
        'Ilquaova1YSFXP5cpD+PKtxRV76Qlqt6o+aPywm81licdOAXotT4JyJhrgz9ISnn\n' \
        'J13KkHoAZ9qd0rX7s37czb3O\n' \
        '-----END PRIVATE KEY-----'

    __REPORT_PUBLIC_KEY_PEM__ = \
        '-----BEGIN PUBLIC KEY-----\n' \
        'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArMvzZi8GT+lI9KeZiInn\n' \
        '4CvFTiuyid+IN4dP1+mhTnfxX+I/ntt8LUKZMbI1R1izOUoxJRoX6VQ4S9VgDLEC\n' \
        'PW6QlkeLI1eqe4DiYb9+J5ANhq4+XkhwgCUUFwpfqSfXWCHimjaGsZHbavl5nv/6\n' \
        'IbZJL/2YzE37IzJdES16JCfmIUrk6TUqL0WgrWXyweTIoVSbld0M29kToSkMXLsj\n' \
        '8vbQbTiKwViWhYlzi0cQIo7PiAss66lAW0X6AM7ZJYyAcfSjSLR4guMz76Og8aRk\n' \
        'jtsjEEkq7Ndz5H8hllWUoHpxGDqLhM9O1/h+QdvTz7luZgpeJ5KB92vYL6yOlSxM\n' \
        'fQIDAQAB\n' \
        '-----END PUBLIC KEY-----'

    _report_private_key = \
        serialization.load_pem_private_key(
            __REPORT_PRIVATE_KEY_PEM__.encode(),
            password=None,
            backend=backends.default_backend())
    _report_public_key = \
        serialization.load_pem_public_key(
            __REPORT_PUBLIC_KEY_PEM__.encode(),
            backend=backends.default_backend())

    # The anti-sybil ID for this particular validator.  This will get set when
    # the enclave is initialized
    _anti_sybil_id = None

    # The PoET keys will remain unset until signup info is either created or
    # unsealed
    _poet_public_key = None
    _poet_private_key = None
    _active_wait_timer = None

    @classmethod
    def initialize(cls, **kwargs):
        # Create an anti-Sybil ID that is unique for this validator
        cls._anti_sybil_id = \
            hashlib.sha256(
                kwargs.get(
                    'NodeName',
                    datetime.datetime.now().isoformat()).encode()).hexdigest()

    @classmethod
    def create_signup_info(cls,
                           originator_public_key_hash,
                           nonce):
        with cls._lock:
            # First we need to create a public/private key pair for the PoET
            # enclave to use.
            cls._poet_private_key = signing.generate_privkey()
            cls._poet_public_key = \
                signing.generate_pubkey(cls._poet_private_key)
            cls._active_wait_timer = None

            # Simulate sealing (encrypting) the signup data.
            signup_data = {
                'poet_public_key': cls._poet_public_key,
                'poet_private_key': cls._poet_private_key
            }
            sealed_signup_data = \
                base64.b64encode(bytes(dict2json(signup_data).encode()))

            # Build up a fake SGX quote containing:
            # 1. The basename
            # 2. The report body that contains:
            #    a. The enclave measurement
            #    b. The report data SHA256(SHA256(OPK)|PPK)
            sgx_basename = \
                sgx_structs.SgxBasename(name=cls.__VALID_BASENAME__)
            sgx_measurement = \
                sgx_structs.SgxMeasurement(
                    m=cls.__VALID_ENCLAVE_MEASUREMENT__)

            hash_input = \
                '{0}{1}'.format(
                    originator_public_key_hash.upper(),
                    cls._poet_public_key.upper()).encode()
            report_data = hashlib.sha256(hash_input).digest()
            sgx_report_data = sgx_structs.SgxReportData(d=report_data)
            sgx_report_body = \
                sgx_structs.SgxReportBody(
                    mr_enclave=sgx_measurement,
                    report_data=sgx_report_data)

            sgx_quote = \
                sgx_structs.SgxQuote(
                    basename=sgx_basename,
                    report_body=sgx_report_body)

            # Create a fake PSE manifest.  A base64 encoding of the
            # originator public key hash should suffice.
            pse_manifest = \
                base64.b64encode(originator_public_key_hash.encode())

            timestamp = datetime.datetime.now().isoformat()

            # Fake our "proof" data.
            verification_report = {
                'epidPseudonym': originator_public_key_hash,
                'id': base64.b64encode(
                    hashlib.sha256(
                        timestamp.encode()).hexdigest().encode()).decode(),
                'isvEnclaveQuoteStatus': 'OK',
                'isvEnclaveQuoteBody':
                    base64.b64encode(sgx_quote.serialize_to_bytes()).decode(),
                'pseManifestStatus': 'OK',
                'pseManifestHash':
                    base64.b64encode(
                        hashlib.sha256(
                            pse_manifest).hexdigest().encode()).decode(),
                'nonce': nonce,
                'timestamp': timestamp
            }

            # Serialize the verification report, sign it, and then put
            # in the proof data
            verification_report_json = dict2json(verification_report)
            signature = \
                cls._report_private_key.sign(
                    verification_report_json.encode(),
                    padding.PKCS1v15(),
                    hashes.SHA256())

            proof_data_dict = {
                'evidence_payload': {
                    'pse_manifest': pse_manifest.decode()
                },
                'verification_report': verification_report_json,
                'signature': base64.b64encode(signature).decode()
            }
            proof_data = dict2json(proof_data_dict)

            return \
                EnclaveSignupInfo(
                    poet_public_key=signup_data['poet_public_key'],
                    proof_data=proof_data,
                    anti_sybil_id=originator_public_key_hash,
                    sealed_signup_data=sealed_signup_data)

    @classmethod
    def deserialize_signup_info(cls, serialized_signup_info):
        return \
            EnclaveSignupInfo.signup_info_from_serialized(
                serialized_signup_info=serialized_signup_info)

    @classmethod
    def unseal_signup_data(cls, sealed_signup_data):
        """

        Args:
            sealed_signup_data: Sealed signup data that was returned
                previously in a EnclaveSignupInfo object from a call to
                create_signup_info

        Returns:
            A string The hex encoded PoET public key that was extracted from
            the sealed data
        """

        # Reverse the process we used in creating "sealed" signup info.
        # Specifically, we will do a base 64 decode, which gives us JSON
        # we can convert back to a dictionary we can use to get the
        # data we need
        signup_data = \
            json2dict(base64.b64decode(sealed_signup_data).decode())

        with cls._lock:
            cls._poet_public_key = str(signup_data.get('poet_public_key'))
            cls._poet_private_key = str(signup_data.get('poet_private_key'))
            cls._active_wait_timer = None

            return signup_data.get('poet_public_key')

    @classmethod
    def verify_signup_info(cls,
                           signup_info,
                           originator_public_key_hash):
        # Verify the attestation verification report signature
        proof_data_dict = json2dict(signup_info.proof_data)
        verification_report = proof_data_dict.get('verification_report')
        if verification_report is None:
            raise ValueError('Verification report is missing from proof data')

        signature = proof_data_dict.get('signature')
        if signature is None:
            raise ValueError('Signature is missing from proof data')

        try:
            cls._report_public_key.verify(
                base64.b64decode(signature.encode()),
                verification_report.encode(),
                padding.PKCS1v15(),
                hashes.SHA256())
        except InvalidSignature:
            raise ValueError('Verification report signature is invalid')

        verification_report_dict = json2dict(verification_report)

        # Verify that the verification report contains an ID field
        if 'id' not in verification_report_dict:
            raise ValueError('Verification report does not contain an ID')

        # Verify that the verification report contains an EPID pseudonym and
        # that it matches the anti-Sybil ID
        epid_pseudonym = verification_report_dict.get('epidPseudonym')
        if epid_pseudonym is None:
            raise \
                ValueError(
                    'Verification report does not contain an EPID pseudonym')

        if epid_pseudonym != signup_info.anti_sybil_id:
            raise \
                ValueError(
                    'The anti-Sybil ID in the verification report [{0}] does '
                    'not match the one contained in the signup information '
                    '[{1}]'.format(
                        epid_pseudonym,
                        signup_info.anti_sybil_id))

        # Verify that the verification report contains a PSE manifest status
        # and it is OK
        pse_manifest_status = \
            verification_report_dict.get('pseManifestStatus')
        if pse_manifest_status is None:
            raise \
                ValueError(
                    'Verification report does not contain a PSE manifest '
                    'status')
        if pse_manifest_status.upper() != 'OK':
            raise \
                ValueError(
                    'PSE manifest status is {} (i.e., not OK)'.format(
                        pse_manifest_status))

        # Verify that the verification report contains a PSE manifest hash
        pse_manifest_hash = \
            verification_report_dict.get('pseManifestHash')
        if pse_manifest_hash is None:
            raise \
                ValueError(
                    'Verification report does not contain a PSE manifest '
                    'hash')

        # Verify that the proof data contains evidence payload
        evidence_payload = proof_data_dict.get('evidence_payload')
        if evidence_payload is None:
            raise ValueError('Evidence payload is missing from proof data')

        # Verify that the evidence payload contains a PSE manifest and then
        # use it to make sure that the PSE manifest hash is what we expect
        pse_manifest = evidence_payload.get('pse_manifest')
        if pse_manifest is None:
            raise ValueError('Evidence payload does not include PSE manifest')

        expected_pse_manifest_hash = \
            base64.b64encode(
                hashlib.sha256(
                    pse_manifest.encode()).hexdigest().encode()).decode()

        if pse_manifest_hash.upper() != expected_pse_manifest_hash.upper():
            raise \
                ValueError(
                    'PSE manifest hash {0} does not match {1}'.format(
                        pse_manifest_hash,
                        expected_pse_manifest_hash))

        # Verify that the verification report contains an enclave quote status
        # and the status is OK
        enclave_quote_status = \
            verification_report_dict.get('isvEnclaveQuoteStatus')
        if enclave_quote_status is None:
            raise \
                ValueError(
                    'Verification report does not contain an enclave quote '
                    'status')
        if enclave_quote_status.upper() != 'OK':
            raise \
                ValueError(
                    'Enclave quote status is {} (i.e., not OK)'.format(
                        enclave_quote_status))

        # Verify that the verification report contains an enclave quote
        enclave_quote = verification_report_dict.get('isvEnclaveQuoteBody')
        if enclave_quote is None:
            raise \
                ValueError(
                    'Verification report does not contain an enclave quote')

        # The ISV enclave quote body is base 64 encoded, so decode it and then
        # create an SGX quote structure from it so we can inspect
        sgx_quote = sgx_structs.SgxQuote()
        sgx_quote.parse_from_bytes(base64.b64decode(enclave_quote))

        # The report body should be SHA256(SHA256(OPK)|PPK)
        #
        # NOTE - since the code that created the report data is in the enclave
        # code, this code needs to be kept in sync with it.  Any changes to how
        # the report data is created, needs to be reflected in how we re-create
        # the report data for verification.

        hash_input = \
            '{0}{1}'.format(
                originator_public_key_hash.upper(),
                cls._poet_public_key.upper()).encode()
        hash_value = hashlib.sha256(hash_input).digest()
        expected_report_data = \
            hash_value + \
            (b'\x00' *
             (sgx_structs.SgxReportData.STRUCT_SIZE - len(hash_value)))

        if sgx_quote.report_body.report_data.d != expected_report_data:
            raise \
                ValueError(
                    'AVR report data [{0}] not equal to [{1}]'.format(
                        sgx_quote.report_body.report_data.d.hex(),
                        expected_report_data.hex()))

        # Compare the enclave measurement against the expected valid enclave
        # measurement.
        #
        # NOTE - this is only a temporary check.  Instead of checking against
        # a predefined enclave measurement value, we should be configured with
        # a set of one or more enclave measurement values that we will
        # consider as valid.

        if sgx_quote.report_body.mr_enclave.m != \
                cls.__VALID_ENCLAVE_MEASUREMENT__:
            raise \
                ValueError(
                    'AVR enclave measurement [{0}] not equal to [{1}]'.format(
                        sgx_quote.report_body.mr_enclave.m.hex(),
                        cls.__VALID_ENCLAVE_MEASUREMENT__.hex()))

        # Compare the enclave basename in the verification report against the
        # expected enclave basename.
        #
        # NOTE - this is only a temporary check.  Instead of checking against
        # a predefined enclave basenme value, we should be configured with a
        # set of one or more enclave basenames that we will consider as valid.

        if sgx_quote.basename.name != cls.__VALID_BASENAME__:
            raise \
                ValueError(
                    'AVR enclave basename [{0}] not equal to [{1}]'.format(
                        sgx_quote.basename.name.hex(),
                        cls.__VALID_BASENAME__.hex()))

    @classmethod
    def create_wait_timer(cls,
                          validator_address,
                          previous_certificate_id,
                          local_mean,
                          minimum_wait_time):
        with cls._lock:
            # If we don't have a PoET private key, then the enclave has not
            # been properly initialized (either by calling create_signup_info
            # or unseal_signup_data)
            if cls._poet_private_key is None:
                raise \
                    ValueError(
                        'Enclave must be initialized before attempting to '
                        'create a wait timer')

            # Create some value from the cert ID.  We are just going to use
            # the seal key to sign the cert ID.  We will then use the
            # low-order 64 bits to change that to a number [0, 1]
            tag = \
                base64.b64decode(
                    signing.sign(
                        previous_certificate_id,
                        cls._seal_private_key))

            tagd = float(struct.unpack('Q', tag[-8:])[0]) / (2**64 - 1)

            # Now compute the duration with a minimum wait time guaranteed
            duration = minimum_wait_time - local_mean * math.log(tagd)

            # Create and sign the wait timer
            wait_timer = \
                EnclaveWaitTimer(
                    validator_address=validator_address,
                    duration=duration,
                    previous_certificate_id=previous_certificate_id,
                    local_mean=local_mean)
            wait_timer.signature = \
                signing.sign(
                    wait_timer.serialize(),
                    cls._poet_private_key)

            # Keep track of the active wait timer
            cls._active_wait_timer = wait_timer

            return wait_timer

    @classmethod
    def deserialize_wait_timer(cls, serialized_timer, signature):
        with cls._lock:
            # Verify the signature before trying to deserialize
            if not signing.verify(
                    serialized_timer,
                    signature,
                    cls._poet_public_key):
                return None

        return \
            EnclaveWaitTimer.wait_timer_from_serialized(
                serialized_timer=serialized_timer,
                signature=signature)

    @classmethod
    def create_wait_certificate(cls,
                                wait_timer,
                                block_hash):
        with cls._lock:
            # If we don't have a PoET private key, then the enclave has not
            # been properly initialized (either by calling create_signup_info
            # or unseal_signup_data)
            if cls._poet_private_key is None:
                raise \
                    ValueError(
                        'Enclave must be initialized before attempting to '
                        'create a wait certificate')

            # Several criteria need to be met before we can create a wait
            # certificate:
            # 1. We have an active timer
            # 2. The caller's wait timer is the active wait timer.  We are not
            #    going to rely the objects, being the same, but will compute
            #    a signature over the object and verify that the signatures
            #    are the same.
            # 3. The active timer has expired
            # 4. The active timer has not timed out
            #
            # Note - we make a concession for the genesis block (i.e., a wait
            # timer for which the previous certificate ID is the Null
            # identifier) in that we don't require the timer to have expired
            # and we don't worry about the timer having timed out.
            if cls._active_wait_timer is None:
                raise \
                    ValueError(
                        'There is not a current enclave active wait timer')

            if wait_timer is None or \
                    cls._active_wait_timer.signature != \
                    signing.sign(
                        wait_timer.serialize(),
                        cls._poet_private_key):
                raise \
                    ValueError(
                        'Validator is not using the current wait timer')

            is_not_genesis_block = \
                (cls._active_wait_timer.previous_certificate_id !=
                 NULL_BLOCK_IDENTIFIER)

            now = time.time()
            expire_time = \
                cls._active_wait_timer.request_time + \
                cls._active_wait_timer.duration

            if is_not_genesis_block and now < expire_time:
                raise \
                    ValueError(
                        'Cannot create wait certificate because timer has '
                        'not expired')

            time_out_time = \
                cls._active_wait_timer.request_time + \
                cls._active_wait_timer.duration + \
                TIMER_TIMEOUT_PERIOD

            if is_not_genesis_block and time_out_time < now:
                raise \
                    ValueError(
                        'Cannot create wait certificate because timer '
                        'has timed out')

            # Create a random nonce for the certificate.  For our "random"
            # nonce we will take the timer signature, concat that with the
            # current time, JSON-ize it and create a SHA-256 hash over it.
            # Probably not considered random by security professional
            # standards, but it is good enough for the simulator.
            random_string = \
                dict2json({
                    'wait_timer_signature': cls._active_wait_timer.signature,
                    'now': datetime.datetime.utcnow().isoformat()
                })
            nonce = hashlib.sha256(random_string.encode()).hexdigest()

            # First create a new enclave wait certificate using the data
            # provided and then sign the certificate with the PoET private key
            wait_certificate = \
                EnclaveWaitCertificate.wait_certificate_with_wait_timer(
                    wait_timer=cls._active_wait_timer,
                    nonce=nonce,
                    block_hash=block_hash)
            wait_certificate.signature = \
                signing.sign(
                    wait_certificate.serialize(),
                    cls._poet_private_key)

            # Now that we have created the certificate, we no longer have an
            # active timer
            cls._active_wait_timer = None

            return wait_certificate

    @classmethod
    def deserialize_wait_certificate(cls, serialized_certificate, signature):
        return \
            EnclaveWaitCertificate.wait_certificate_from_serialized(
                serialized_certificate=serialized_certificate,
                signature=signature)

    @classmethod
    def verify_wait_certificate(cls, certificate, poet_public_key):
        # Since the signing module uses a hex-encoded string as the canonical
        # format for public keys and we should be handed a public key that was
        # part of signup information created by us, don't bother decoding
        # the public key.
        if not \
            signing.verify(
                certificate.serialize(),
                certificate.signature,
                poet_public_key):
            raise ValueError('Wait certificate signature does not match')


def initialize(**kwargs):
    _PoetEnclaveSimulator.initialize(**kwargs)


def create_signup_info(validator_address,
                       originator_public_key_hash,
                       nonce):
    return \
        _PoetEnclaveSimulator.create_signup_info(
            originator_public_key_hash=originator_public_key_hash,
            nonce=nonce)


def deserialize_signup_info(serialized_signup_info):
    return _PoetEnclaveSimulator.deserialize_signup_info(
        serialized_signup_info=serialized_signup_info)


def unseal_signup_data(validator_address, sealed_signup_data):
    return _PoetEnclaveSimulator.unseal_signup_data(sealed_signup_data)


def verify_signup_info(signup_info,
                       originator_public_key_hash):
    return \
        _PoetEnclaveSimulator.verify_signup_info(
            signup_info=signup_info,
            originator_public_key_hash=originator_public_key_hash)


def create_wait_timer(validator_address,
                      previous_certificate_id,
                      local_mean,
                      minimum_wait_time=1.0):
    return \
        _PoetEnclaveSimulator.create_wait_timer(
            validator_address=validator_address,
            previous_certificate_id=previous_certificate_id,
            local_mean=local_mean,
            minimum_wait_time=minimum_wait_time)


def deserialize_wait_timer(serialized_timer, signature):
    return \
        _PoetEnclaveSimulator.deserialize_wait_timer(
            serialized_timer=serialized_timer,
            signature=signature)


def create_wait_certificate(wait_timer, block_hash):
    return \
        _PoetEnclaveSimulator.create_wait_certificate(
            wait_timer=wait_timer,
            block_hash=block_hash)


def deserialize_wait_certificate(serialized_certificate, signature):
    return \
        _PoetEnclaveSimulator.deserialize_wait_certificate(
            serialized_certificate=serialized_certificate,
            signature=signature)


def verify_wait_certificate(certificate, poet_public_key):
    _PoetEnclaveSimulator.verify_wait_certificate(
        certificate=certificate,
        poet_public_key=poet_public_key)
