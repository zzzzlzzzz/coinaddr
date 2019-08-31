# pylint: disable=no-member

"""
:mod:`coinaddr.validation`
~~~~~~~~~~~~~~~~~~~~~~~~

Various validation machinery for validating cryptocurrency addresses.
"""

import re
from hashlib import sha256
import functools
import operator
from binascii import unhexlify

from zope.interface import implementer, provider
import attr
import sha3
import base58check

from .interfaces import (
    INamedSubclassContainer, IValidator, IValidationRequest,
    IValidationResult, ICurrency
    )
from .base import NamedSubclassContainerBase
from . import currency, base58_xmr
from .exceptions import CoinaddrException


@provider(INamedSubclassContainer)
class Validators(metaclass=NamedSubclassContainerBase):
    """Container for all validators."""


class ValidatorMeta(type):
    """Register validator classes on Validators.validators."""

    def __new__(mcs, cls, bases, attrs):
        new = type.__new__(mcs, cls, bases, attrs)
        if new.name:
            Validators[new.name] = new
        return new


@attr.s(cmp=False, slots=True)
class ValidatorBase(metaclass=ValidatorMeta):
    """Validator Interface."""

    name = None

    request = attr.ib(
        type='ValidationRequest',
        validator=[
            lambda i, a, v: type(v).__name__ == 'ValidationRequest',
            attr.validators.provides(IValidationRequest)
            ]
    )

    def validate(self):
        """Validate the address type, return True if valid, else False."""

    @property
    def network(self):
        """Return the network derived from the network version bytes."""


@attr.s(frozen=True, slots=True, cmp=False)
@implementer(IValidator)
class Base58CheckValidator(ValidatorBase):
    """Validates Base58Check based cryptocurrency addresses."""

    name = 'Base58Check'

    def validate(self):
        """Validate the address."""
        if 25 > len(self.request.address) > 35:
            return False

        abytes = base58check.b58decode(
            self.request.address, **self.request.extras)
        if not abytes[0] in self.request.networks:
            return False

        checksum = sha256(sha256(abytes[:-4]).digest()).digest()[:4]
        if abytes[-4:] != checksum:
            return False

        return self.request.address == base58check.b58encode(
            abytes, **self.request.extras)

    @property
    def network(self):
        """Return network derived from network version bytes."""
        abytes = base58check.b58decode(
            self.request.address, **self.request.extras)

        nbyte = abytes[0]
        for name, networks in self.request.currency.networks.items():
            if nbyte in networks:
                return name


@attr.s(frozen=True, slots=True, cmp=False)
@implementer(IValidator)
class EthereumValidator(ValidatorBase):
    """Validates ethereum based crytocurrency addresses."""

    name = 'Ethereum'
    non_checksummed_patterns = (
        re.compile("^(0x)?[0-9a-f]{40}$"), re.compile("^(0x)?[0-9A-F]{40}$")
        )

    def validate(self):
        """Validate the address."""
        address = self.request.address.decode()
        if any(bool(pat.match(address))
               for pat in self.non_checksummed_patterns):
            return True
        addr = address[2:] if address.startswith('0x') else address
        addr_hash = sha3.keccak_256(addr.lower().encode('ascii')).hexdigest()
        for i, letter in enumerate(addr):
            if any([
                    int(addr_hash[i], 16) >= 8 and letter.upper() != letter,
                    int(addr_hash[i], 16) < 8 and letter.lower() != letter
            ]):
                return False
        return True

    @property
    def network(self):
        """Return network derived from network version bytes."""
        return 'both'


@attr.s(frozen=True, slots=True, cmp=False)
@implementer(IValidator)
class MoneroValidator(ValidatorBase):
    """Validates monero based cryptocurrency addresses."""

    name = 'Monero'
    address_regex = re.compile(r'^[123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]{95}$')
    integrated_address_regex = re.compile(r'^[123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]{106}$')

    @staticmethod
    def decode(address):
        """Return tuple (netbyte, valid checksum)"""
        decoded = bytearray(unhexlify(base58_xmr.decode(address)))
        return decoded[0], decoded[-4:] == sha3.keccak_256(decoded[:-4]).digest()[:4]

    def validate(self):
        """Validate the address"""
        address = self.request.address.decode()
        if self.address_regex.match(address):
            netbyte, valid = self.decode(address)
            return (netbyte in (self.request.currency.networks['main'] +
                                self.request.currency.networks['test'] +
                                self.request.currency.networks['stage'])) and valid
        elif self.integrated_address_regex.match(address):
            netbyte, valid = self.decode(address)
            return (netbyte in (self.request.currency.networks['main_integrated'] +
                                self.request.currency.networks['test_integrated'] +
                                self.request.currency.networks['stage_integrated'])) and valid
        return False

    @property
    def network(self):
        """Return network derived from network version bytes"""
        netbyte, valid = self.decode(self.request.address.decode())
        for name, networks in self.request.currency.networks.items():
            if netbyte in networks:
                return name


@attr.s(frozen=True, slots=True, cmp=False)
@implementer(IValidationRequest)
class ValidationRequest:
    """Contain the data and helpers as an immutable request object."""

    currency = attr.ib(
        type=currency.Currency,
        converter=currency.Currencies.get,
        validator=[
            attr.validators.instance_of(currency.Currency),
            attr.validators.provides(ICurrency)
            ])
    address = attr.ib(
        type=bytes,
        converter=lambda a: a if isinstance(a, bytes) else a.encode('ascii'),
        validator=attr.validators.instance_of(bytes))

    @property
    def extras(self):
        """Extra arguments for passing to decoder, etc."""
        extras = dict()
        if self.currency.charset:
            extras.setdefault('charset', self.currency.charset)
        return extras

    @property
    def networks(self):
        """Concatenated list of all version bytes for currency."""
        networks = tuple(self.currency.networks.values())
        return functools.reduce(operator.concat, networks)

    def execute(self):
        """Execute this request and return the result."""
        validator = Validators.get(self.currency.validator)(self)
        try:
            return ValidationResult(
                name=self.currency.name,
                ticker=self.currency.ticker,
                address=self.address,
                valid=validator.validate(),
                network=validator.network
                )
        except Exception as e:
            raise CoinaddrException(e)


@attr.s(frozen=True, slots=True, cmp=False)
@implementer(IValidationResult)
class ValidationResult:
    """Contains an immutable representation of the validation result."""

    name = attr.ib(
        type=str,
        validator=attr.validators.instance_of(str))
    ticker = attr.ib(
        type=str,
        validator=attr.validators.instance_of(str))
    address = attr.ib(
        type=bytes,
        validator=attr.validators.instance_of(bytes))
    valid = attr.ib(
        type=bool,
        validator=attr.validators.instance_of(bool))
    network = attr.ib(
        type=str,
        validator=attr.validators.instance_of(str))

    def __bool__(self):
        return self.valid


def validate(currency, address):
    """Validate the given address according to currency type.

    This is the main entrypoint for using this library.

    :param currency str: The name or ticker code of the cryptocurrency.
    :param address (bytes, str): The crytocurrency address to validate.
    :return: a populated ValidationResult object
    :rtype: :inst:`ValidationResult`

    Usage::

      >>> import coinaddr
      >>> coinaddr.validate('btc', b'1BoatSLRHtKNngkdXEeobR76b53LETtpyT')
      ValidationResult(name='bitcoin', ticker='btc',
      ...              address=b'1BoatSLRHtKNngkdXEeobR76b53LETtpyT',
      ...              valid=True, network='main')

    """
    request = ValidationRequest(currency, address)
    return request.execute()
