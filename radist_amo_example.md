# Example: 3 matched deals (amo -> Radist)

Below is a schematic table (like a Supabase row view).  
`dialog_raw` and `comment` are stored but shown here truncated.

| tenant_id | deal_id | deal_name | status | responsible | phone | chat_id | first_message_at | last_message_at | messages_count | deal_attrs_json | contact_attrs_json | dialog_raw | comment |
|---|---:|---|---|---|---|---:|---|---|---:|---|---|---|---|
| globalfruit | 50902237 | Эмбриолог МЦ "Fertility" | на доставке | gfamomanager1@outlook.com | +7 701 203 74 44 | 38685634 | 2026-02-19T06:32:51Z | 2026-02-19T07:57:12Z | 39 | {"source":"Other service"} | {"name":"Эмбриолог МЦ \"Fertility\""} | `06:41 inbound Вам не видно заказ из корзины? ...` | |
| globalfruit | 50831709 | Дильбар Тахирова | на доставке | gfamomanager1@outlook.com | +7 708 119 77 27 | 38644791 | 2026-02-18T12:42:09Z | 2026-02-19T08:00:01Z | 56 | {} | {"name":"Дильбар Тахирова"} | `12:42 inbound ... 12:50 outbound ...` | |
| globalfruit | 50901693 | Динара Нургазина | на доставке | gfamomanager1@outlook.com | +7 701 511 55 48 | 38685373 | 2026-02-19T06:17:09Z | 2026-02-19T08:50:48Z | 25 | {} | {"name":"Динара Нургазина"} | `06:17 inbound ... 06:20 outbound ...` | |
