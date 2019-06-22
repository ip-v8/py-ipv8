from __future__ import absolute_import

from functools import wraps

from ipv8.keyvault.private.libnaclkey import LibNaCLSK
from .messaging.payload_headers import BinMemberAuthenticationPayload, GlobalTimeDistributionPayload
from .keyvault.crypto import default_eccrypto
from .overlay import Overlay
from .peer import Peer
from .util import cast_to_bin

from rust_ipv8_in_python import borrowed_bundler

def lazy_wrapper(*payloads):
    """
    This function wrapper will unpack the BinMemberAuthenticationPayload for you.

    You can now write your authenticated and signed functions as follows:

    ::

        @lazy_wrapper(GlobalTimeDistributionPayload, IntroductionRequestPayload, IntroductionResponsePayload)
        def on_message(peer, payload1, payload2):
            '''
            :type peer: Peer
            :type payload1: IntroductionRequestPayload
            :type payload2: IntroductionResponsePayload
            '''
            pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, source_address, data):
            # UNPACK
            auth, remainder = self.serializer.unpack_to_serializables([BinMemberAuthenticationPayload, ], data[23:])
            signature_valid, remainder = self._verify_signature(auth, data)
            unpacked = self.serializer.ez_unpack_serializables(payloads, remainder)
            # ASSERT
            if not signature_valid:
                raise PacketDecodingError("Incoming packet %s has an invalid signature" %
                                          str([payload_class.__name__ for payload_class in payloads]))
            # PRODUCE
            return func(self, Peer(auth.public_key_bin, source_address), *unpacked)
        return wrapper
    return decorator


def lazy_wrapper_wd(*payloads):
    """
    This function wrapper will unpack the BinMemberAuthenticationPayload for you, as well as pass the raw data to the
    decorated function

    You can now write your authenticated and signed functions as follows:

    ::

        @lazy_wrapper(GlobalTimeDistributionPayload, IntroductionRequestPayload, IntroductionResponsePayload)
        def on_message(peer, payload1, payload2, data):
            '''
            :type peer: Peer
            :type payload1: IntroductionRequestPayload
            :type payload2: IntroductionResponsePayload
            '''
            pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, source_address, data):
            # UNPACK
            auth, remainder = self.serializer.unpack_to_serializables([BinMemberAuthenticationPayload, ], data[23:])
            signature_valid, remainder = self._verify_signature(auth, data)
            unpacked = self.serializer.ez_unpack_serializables(payloads, remainder)
            # ASSERT
            if not signature_valid:
                raise PacketDecodingError("Incoming packet %s has an invalid signature" %
                                          str([payload_class.__name__ for payload_class in payloads]))
            # PRODUCE
            output = unpacked + [data]
            return func(self, Peer(auth.public_key_bin, source_address), *output)
        return wrapper
    return decorator


def lazy_wrapper_unsigned(*payloads):
    """
    This function wrapper will unpack just the normal payloads for you.

    You can now write your non-authenticated and signed functions as follows:

    ::

        @lazy_wrapper_unsigned(GlobalTimeDistributionPayload, IntroductionRequestPayload, IntroductionResponsePayload)
        def on_message(source_address, payload1, payload2):
            '''
            :type source_address: str
            :type payload1: IntroductionRequestPayload
            :type payload2: IntroductionResponsePayload
            '''
            pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, source_address, data):
            # UNPACK
            unpacked = self.serializer.ez_unpack_serializables(payloads, data[23:])
            return func(self, source_address, *unpacked)
        return wrapper
    return decorator


def lazy_wrapper_unsigned_wd(*payloads):
    """
    This function wrapper will unpack just the normal payloads for you, as well as pass the raw data to the decorated
    function

    You can now write your non-authenticated and signed functions as follows:

    ::

        @lazy_wrapper_unsigned_wd(GlobalTimeDistributionPayload, IntroductionRequestPayload,
        IntroductionResponsePayload)
        def on_message(source_address, payload1, payload2, data):
            '''
            :type source_address: str
            :type payload1: IntroductionRequestPayload
            :type payload2: IntroductionResponsePayload
            '''
            pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, source_address, data):

            @lazy_wrapper_unsigned(*payloads)
            def inner_wrapper(inner_self, inner_source_address, *pyls):
                combo = list(pyls) + [data]
                return func(inner_self, inner_source_address, *combo)

            return inner_wrapper(self, source_address, data)
        return wrapper
    return decorator


class EZPackOverlay(Overlay):

    def ezr_pack(self, msg_num, *payloads, **kwargs):
        """
        The easier way to pack your messages. Supply with the message number and the Payloads you want to serialize.
        Optionally you can choose to sign the message.

        :param msg_num: the message number to claim for this message
        :type msg_num: int
        :param sig: whether or not to sign this message
        :type sig: bool
        :param payloads: the list of Payload instances to serialize
        :type payloads: [Payload]
        :return: the serialized message
        :rtype: bytes or str
        """
        sig = kwargs.get('sig', True)
        format_list_list = ([BinMemberAuthenticationPayload(self.my_peer.public_key.key_to_bin()).to_pack_list()]
                            if sig else [])
        for payload in payloads:
            format_list_list += [payload.to_pack_list()]
        return self._ez_pack(self._prefix, msg_num, format_list_list, sig)

    def _ez_pack(self, prefix, msg_num, format_list_list, sig=True):
        packet = prefix + cast_to_bin(chr(msg_num))
        for format_list in format_list_list:
            packet += self.serializer.pack_multiple(format_list)[0]
        if sig:
            if type(self.my_peer.key) == LibNaCLSK:
                key = self.my_peer.key.veri.vk
                seed = self.my_peer.key.key.seed

                packet = borrowed_bundler.borrowed_create_signature(seed, key, packet)
            else:
                packet += default_eccrypto.create_signature(self.my_peer.key, packet)

        return packet

    def _verify_signature(self, auth, data):
        # rust verifies ed25519 signatures
        if auth.public_key_bin.startswith(b"LibNaCLPK:"):
            return borrowed_bundler.borrowed_verify_signature(auth.public_key_bin, data)

        # fallback: python verification kept as rust
        # deprecates openssl verification so this is kept slow.
        ec = default_eccrypto
        public_key = ec.key_from_public_bin(auth.public_key_bin)
        signature_length = ec.get_signature_length(public_key)
        # remove header + binmemberauth and signature
        # 23 + 2(len) + x bytes of public key and stop n bytes before the end where
        # n is signature length
        remainder = data[23 + 2 + len(auth.public_key_bin):-signature_length]
        signature = data[-signature_length:]
        return ec.is_valid_signature(public_key, data[:-signature_length], signature), remainder

    def _ez_unpack_auth(self, payload_class, data):
        # UNPACK
        auth, remainder = self.serializer.unpack_to_serializables([BinMemberAuthenticationPayload, ], data[23:])
        signature_valid, remainder = self._verify_signature(auth, data)
        format = [GlobalTimeDistributionPayload, payload_class]
        dist, payload = self.serializer.ez_unpack_serializables(format, remainder)
        # ASSERT
        if not signature_valid:
            raise PacketDecodingError("Incoming packet %s has an invalid signature" % payload_class.__name__)
        # PRODUCE
        return auth, dist, payload

    def _ez_unpack_noauth(self, payload_class, data, global_time=True):
        # UNPACK
        format = [GlobalTimeDistributionPayload, payload_class] if global_time else [payload_class]
        unpacked = self.serializer.ez_unpack_serializables(format, data[23:])
        # PRODUCE
        return unpacked if global_time else unpacked[0]


class PacketDecodingError(RuntimeError):
    pass
