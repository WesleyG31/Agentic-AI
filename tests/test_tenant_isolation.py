from kompass.actions.executor import ActionReceipt, ActionStatus, InMemoryReceiptStore
from kompass.api.app import _config
from kompass.runtime import RuntimeContext, tenant_scoped_id


def test_same_external_conversation_id_maps_to_different_storage_keys():
    tenant_a = RuntimeContext.create(tenant_id="tenant-a", user_id="same-user")
    tenant_b = RuntimeContext.create(tenant_id="tenant-b", user_id="same-user")

    assert tenant_scoped_id(tenant_a, "thread-1") != tenant_scoped_id(tenant_b, "thread-1")
    assert _config("thread-1", tenant_a) != _config("thread-1", tenant_b)


def test_action_receipt_lookup_cannot_cross_tenant():
    store = InMemoryReceiptStore()
    store.put(
        ActionReceipt(
            action="create_refund",
            idempotency_key="same-key",
            tenant_id="tenant-a",
            status=ActionStatus.SUCCEEDED,
        )
    )

    assert store.get("tenant-a", "same-key") is not None
    assert store.get("tenant-b", "same-key") is None
