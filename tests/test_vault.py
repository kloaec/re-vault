import bitcoin
import pytest
import random
import unittest

from bip32 import BIP32
from bitcoin.core import b2x, COIN
from bitcoin.wallet import CKey
from fixtures import *  # noqa: F401,F403
from utils import SIGSERV_URL, COSIGNER_URL, wait_for


bitcoin.SelectParams("regtest")


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_vault_address(vault_factory):
    vaults = vault_factory.get_vaults()
    # FIXME: separate the Bitcoin backends !!
    bitcoind = vaults[0].bitcoind
    for vault in vaults:
        # It's burdensome to our xpub to be None in the list, but it allows us
        # to know which of the stakeholders we are, so..
        all_xpubs = [keychain.get_master_xpub() if keychain
                     else vault.our_bip32.get_master_xpub()
                     for keychain in vault.keychains]
        # bitcoind should always return the same address as us
        for i in range(3):
            vault_first_address = vault.getnewaddress()
            bitcoind_first_address = bitcoind.addmultisigaddress(4, [
                b2x(BIP32.from_xpub(xpub).get_pubkey_from_path([i]))
                for xpub in all_xpubs
            ])["address"]
            assert vault_first_address == bitcoind_first_address


def test_sigserver(bitcoind, sigserv):
    """We just test that it stores sigs correctly."""
    sig = "a01f"
    txid = "0101"
    stk_id = 1
    # POST a dummy sig
    r = sigserv.post("sig/{}/{}".format(txid, stk_id),
                     data={"sig": sig})
    assert r.status_code == 201
    assert r.json == {"sig": sig}
    # GET it
    r = sigserv.get("sig/{}/{}".format(txid, stk_id))
    assert r.status_code == 200
    assert r.json == {"sig": sig}


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_sigserver_feerate(vault_factory):
    """We just test that it gives us a (valid) feerate."""
    # FIXME: Test that it sends the same feerate with same txid
    vault = vault_factory.get_vaults()[0]
    # GET emergency feerate
    feerate = vault.sigserver.get_feerate("emergency", txid="high_entropy")
    # sats/vbyte, if it's less there's something going on !
    assert feerate >= 1


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_signatures_posting(vault_factory):
    """Test that we can send signatures to the sig server."""
    vault = vault_factory.get_vaults()[0]
    # Who am I ?
    stk_id = vault.keychains.index(None) + 1
    vault.sigserver.send_signature("00af", "aa56", stk_id)


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_funds_polling(vault_factory):
    """Test that we are aware of the funds we receive."""
    vault = vault_factory.get_vaults()[0]
    # FIXME: separate the Bitcoin backends !!
    bitcoind = vault.bitcoind
    assert len(vault.vaults) == 0
    # Send new funds to it
    for i in range(3):
        bitcoind.pay_to(vault.getnewaddress(), 10)
    wait_for(lambda: len(vault.vaults) == 3)
    # Retry with a gap
    for _ in range(20):
        vault.getnewaddress()
    for i in range(2):
        bitcoind.pay_to(vault.getnewaddress(), 10)
    wait_for(lambda: len(vault.vaults) == 5)


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_emergency_sig_sharing(vault_factory):
    """Test that we share the emergency transaction signature."""
    vault = vault_factory.get_vaults()[0]
    # FIXME: separate the Bitcoin backends !!
    bitcoind = vault.bitcoind
    assert len(vault.vaults) == 0
    # Send new funds to it
    bitcoind.pay_to(vault.getnewaddress(), 10)
    wait_for(lambda: len(vault.vaults) == 1)
    # We send then request it, hence if we succesfully requested it, we
    # succesfully delivered it to the sig server
    wait_for(lambda: len(vault.vaults[0]["emergency_sigs"]) > 0)


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_emergency_tx_sync(vault_factory):
    """Test that we correctly share and gather emergency transactions
    signatures."""
    vaults = vault_factory.get_vaults()
    # FIXME: separate the Bitcoin backends !!
    bitcoind = vaults[0].bitcoind
    # Sending funds to any vault address will be remarked by anyone
    for vault in vaults:
        bitcoind.pay_to(vault.getnewaddress(), 10)
    for vault in vaults:
        wait_for(lambda: len(vault.vaults) == len(vaults))
        # FIXME: too much "vault" vars
        wait_for(lambda: all(v["emergency_signed"] for v in vault.vaults))
    # All nodes should have the same emergency transactions
    for i in range(len(vaults) - 1):
        first_emer_txs = [v["emergency_tx"] for v in vaults[i].vaults]
        second_emer_txs = [v["emergency_tx"] for v in vaults[i + 1].vaults]
        for tx in first_emer_txs:
            assert tx == second_emer_txs[first_emer_txs.index(tx)]


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_emergency_broadcast(vault_factory):
    """Test that all the emergency transactions we create are valid and can be
    broadcast."""
    vaults = vault_factory.get_vaults()
    # FIXME: separate the Bitcoin backends !!
    bitcoind = vaults[0].bitcoind
    # Sending funds to any vault address will be remarked by anyone
    for vault in vaults:
        for _ in range(2):
            bitcoind.pay_to(vault.getnewaddress(), 10)
    wait_for(lambda: all(v["emergency_signed"] for v in vault.vaults))
    vault = random.choice(vaults)
    for tx in [v["emergency_tx"] for v in vault.vaults]:
        bitcoind.broadcast_and_mine(b2x(tx.serialize()))
    wait_for(lambda: len(vault.vaults) == 0)


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_vault_address_reuse(vault_factory):
    """Test that we are still safe if coins are sent to an already used vault.
    """
    vaults = vault_factory.get_vaults()
    # FIXME: separate the Bitcoin backends !!
    bitcoind = vaults[0].bitcoind
    vault = random.choice(vaults)
    address = vault.getnewaddress()
    # Concurrent send to the same address should be fine
    for _ in range(5):
        bitcoind.pay_to(address, 12)
    wait_for(lambda: len(vault.vaults) == 5)
    for vault in vaults:
        wait_for(lambda: all(v["emergency_signed"] and v["unvault_signed"]
                             and v["unvault_secure"] for v in vault.vaults))
    # FIXME: When spend is implemented test address reuse after spend


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_tx_chain_sync(vault_factory):
    """Test all vaults will exchange signatures for all transactions"""
    vaults = vault_factory.get_vaults()
    # FIXME: separate the Bitcoin backends !!
    bitcoind = vaults[0].bitcoind
    # Sending funds to any vault address will be remarked by anyone
    for vault in vaults:
        for _ in range(2):
            bitcoind.pay_to(vault.getnewaddress(), 10)
    wait_for(lambda: all(len(v.vaults) == 8 for v in vaults))
    wait_for(lambda: all(v["emergency_signed"] for v in vault.vaults))
    wait_for(lambda: all(v["unvault_signed"] for v in vault.vaults))
    assert all(v["unvault_secure"] for v in vault.vaults)
    # We can broadcast the unvault tx for any vault
    vault = random.choice(vaults)
    for v in vault.vaults:
        bitcoind.broadcast_and_mine(b2x(v["unvault_tx"].serialize()))
    wait_for(lambda: all(len(v.vaults) == 0 for v in vaults))


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_cancel_unvault(vault_factory):
    """Test the unvault cancelation (cancel_tx *AND* emer_unvault_tx)"""
    vaults = vault_factory.get_vaults()
    # FIXME: separate the Bitcoin backends !!
    bitcoind = vaults[0].bitcoind
    # Sending funds to any vault address will be remarked by anyone
    for vault in vaults:
        bitcoind.pay_to(vault.getnewaddress(), 10)
    wait_for(lambda: all(len(v.vaults) == len(vaults) for v in vaults))
    wait_for(lambda: all(v["emergency_signed"] for v in vault.vaults))
    wait_for(lambda: all(v["unvault_signed"] for v in vault.vaults))
    assert all(v["unvault_secure"] for v in vault.vaults)
    vault = random.choice(vaults)
    # Send some cancel transaction, they pay to the same script, but the old
    # vault is deleted from our view
    for i in [0, 1]:
        bitcoind.broadcast_and_mine(b2x(vault.vaults[i]["unvault_tx"]
                                        .serialize()))
        bitcoind.broadcast_and_mine(b2x(vault.vaults[i]["cancel_tx"]
                                        .serialize()))
    wait_for(lambda: all(len(v.vaults) == 4 for v in vaults))
    # We should exchange all the signatures for the new vault !
    wait_for(lambda: all(v["emergency_signed"] for v in vault.vaults))
    wait_for(lambda: all(v["unvault_signed"] for v in vault.vaults))
    assert all(v["unvault_secure"] for v in vault.vaults)
    # Send some emergency transactions, this time no new vault created !
    for i in [2, 3]:
        bitcoind.broadcast_and_mine(b2x(vault.vaults[i]["unvault_tx"]
                                        .serialize()))
        bitcoind.broadcast_and_mine(b2x(vault.vaults[i]["unvault_emer_tx"]
                                        .serialize()))
    wait_for(lambda: all(len(v.vaults) == 4 for v in vaults))


@unittest.skipIf("" in [SIGSERV_URL, COSIGNER_URL],
                 "We need the servers for the vaults to operate")
def test_spend_creation(vault_factory):
    """Test that the signature exchange between the traders and cosigner leads
    to a well-formed spend_tx."""
    vaults = vault_factory.get_vaults()
    vaultA, vaultB = vaults[0], vaults[1]
    # FIXME: separate the Bitcoin backends !!
    bitcoind = vaultA.bitcoind
    bitcoind.pay_to(vaultA.getnewaddress(), 10)
    wait_for(lambda: all(len(v.vaults) == 1 for v in vaults))
    wait_for(lambda: all(v["emergency_signed"] for v in vaultA.vaults))
    wait_for(lambda: all(v["unvault_signed"] for v in vaultA.vaults))
    # Try to spend from the newly created vault
    v = vaultA.vaults[0]
    # FIXME
    spend_amount = 10 * COIN - 50000
    address = bitcoind.getnewaddress()
    vaultA.initiate_spend(v, spend_amount, address)
    sigB = vaultB.accept_spend(v["txid"], spend_amount, address)
    pubkeyB = CKey(vaultB.vaults[0]["privkey"]).pub
    tx = vaultA.complete_spend(v, pubkeyB, sigB, spend_amount, address)
    bitcoind.broadcast_and_mine(b2x(v["unvault_tx"].serialize()))
    addr = bitcoind.getnewaddress()
    # Timelock
    bitcoind.generatetoaddress(5, addr)
    bitcoind.broadcast_and_mine(b2x(tx.serialize()))