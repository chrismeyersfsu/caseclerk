"""Default field sets requested from Clio.

Clio returns only id/etag unless fields are requested explicitly. One level
of nesting is supported via the curly-brace syntax. Field names come from the
published OpenAPI spec (Contact/Matter schemas).
"""

CONTACT_FIELDS = ",".join(
    [
        "id",
        "etag",
        "type",
        "name",
        "prefix",
        "title",
        "first_name",
        "middle_name",
        "last_name",
        "initials",
        "date_of_birth",
        "created_at",
        "updated_at",
        "is_client",
        "is_co_counsel",
        "is_bill_recipient",
        "clio_connect_email",
        "primary_email_address",
        "secondary_email_address",
        "primary_phone_number",
        "secondary_phone_number",
        "company{id,name}",
        "addresses{id,name,street,city,province,postal_code,country,primary}",
        "email_addresses{id,name,address,primary}",
        "phone_numbers{id,name,number,primary}",
        "web_sites{id,name,address}",
        "instant_messengers{id,name,address}",
        "custom_field_values{id,field_name,field_type,value,soft_deleted}",
    ]
)

MATTER_FIELDS = ",".join(
    [
        "id",
        "etag",
        "number",
        "display_number",
        "custom_number",
        "description",
        "status",
        "location",
        "client_reference",
        "billable",
        "billing_method",
        "open_date",
        "close_date",
        "pending_date",
        "created_at",
        "updated_at",
        "shared",
        "last_activity_date",
        "matter_stage_updated_at",
        "client{id,name,type,primary_email_address,primary_phone_number}",
        "practice_area{id,name}",
        "matter_stage{id,name}",
        "responsible_attorney{id,name,email}",
        "originating_attorney{id,name,email}",
        "responsible_staff{id,name}",
        "statute_of_limitations{id,name,status,due_at}",
        "custom_field_values{id,field_name,field_type,value,soft_deleted}",
    ]
)
