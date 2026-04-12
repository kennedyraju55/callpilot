"""CallPilot Spending Tracker — Check Twilio + OpenAI costs."""

import sys
from datetime import datetime, timezone
from app.config import settings
from twilio.rest import Client


def get_twilio_costs():
    """Fetch Twilio usage for this month."""
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    records = client.usage.records.this_month.list()

    costs = {}
    for r in records:
        price = float(r.price or 0)
        if price > 0:
            costs[r.category] = {"count": r.count, "unit": r.usage_unit, "price": price}

    return costs


def get_call_stats():
    """Get call history and duration stats."""
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    calls = client.calls.list(limit=100)

    total_seconds = 0
    call_list = []
    for c in calls:
        dur = int(c.duration or 0)
        total_seconds += dur
        call_list.append({
            "date": c.date_created.strftime("%Y-%m-%d %H:%M"),
            "to": c.to,
            "status": c.status,
            "duration": dur,
        })

    return call_list, total_seconds


def estimate_openai_costs(total_seconds):
    """Estimate OpenAI Realtime API costs from call duration."""
    total_minutes = total_seconds / 60.0

    # Audio tokens: input = 1 token/100ms, output = 1 token/50ms
    # Assume ~50/50 split between caller and AI speaking
    input_tokens = (total_minutes * 0.5) * 600
    output_tokens = (total_minutes * 0.5) * 1200

    # gpt-4o-realtime-preview pricing (per 1M tokens)
    # Audio input: $100, Audio output: $200
    input_cost = (input_tokens / 1_000_000) * 100
    output_cost = (output_tokens / 1_000_000) * 200
    embed_cost = 0.01  # negligible RAG embedding cost

    return {
        "total_minutes": total_minutes,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "input_cost": input_cost,
        "output_cost": output_cost,
        "embed_cost": embed_cost,
        "total": input_cost + output_cost + embed_cost,
    }


def print_report():
    """Print full spending report."""
    print("\n" + "=" * 55)
    print("  💰 CallPilot Spending Report")
    print("  " + datetime.now().strftime("%B %Y"))
    print("=" * 55)

    # Twilio
    print("\n📞 TWILIO COSTS")
    print("-" * 55)
    twilio_costs = get_twilio_costs()
    twilio_total = 0.0

    labels = {
        "phonenumbers-local": "Phone Number (monthly)",
        "calls-outbound": "Outbound Calls",
        "calls": "All Calls (in+out)",
        "calls-media-stream-minutes": "Media Streams",
        "calls-text-to-speech": "Text-to-Speech",
        "amazon-polly": "Amazon Polly TTS",
    }

    for key, label in labels.items():
        if key in twilio_costs:
            c = twilio_costs[key]
            print(f"  {label:<30} {c['count']:>6} {c['unit']:<10} ${c['price']:>8.4f}")

    # Use totalprice if available, otherwise sum
    if "totalprice" in twilio_costs:
        twilio_total = twilio_costs["totalprice"]["price"]
    else:
        twilio_total = sum(c["price"] for c in twilio_costs.values())

    print(f"  {'':─<50}")
    print(f"  {'Twilio Total':<30} {'':>17} ${twilio_total:>8.4f}")

    # Calls
    print("\n📋 CALL HISTORY")
    print("-" * 55)
    call_list, total_seconds = get_call_stats()
    print(f"  Total Calls: {len(call_list)}")
    print(f"  Total Talk Time: {total_seconds}s ({total_seconds/60:.1f} min)\n")

    print(f"  {'Date':<18} {'To':<16} {'Status':<12} {'Duration':>8}")
    print(f"  {'─'*18} {'─'*16} {'─'*12} {'─'*8}")
    for c in call_list[:15]:
        print(f"  {c['date']:<18} {c['to']:<16} {c['status']:<12} {c['duration']:>6}s")
    if len(call_list) > 15:
        print(f"  ... and {len(call_list) - 15} more calls")

    # OpenAI
    print("\n🤖 OPENAI COSTS (estimated)")
    print("-" * 55)
    oai = estimate_openai_costs(total_seconds)
    print(f"  Audio Input  ({oai['input_tokens']:>6} tokens)     ${oai['input_cost']:>8.4f}")
    print(f"  Audio Output ({oai['output_tokens']:>6} tokens)     ${oai['output_cost']:>8.4f}")
    print(f"  RAG Embeddings                       ${oai['embed_cost']:>8.4f}")
    print(f"  {'':─<50}")
    print(f"  {'OpenAI Total (est.)':<30} {'':>9} ${oai['total']:>8.4f}")
    print(f"\n  ℹ️  For exact OpenAI costs: https://platform.openai.com/usage")

    # Grand total
    grand_total = twilio_total + oai["total"]
    print("\n" + "=" * 55)
    print(f"  📊 GRAND TOTAL")
    print(f"  Twilio:  ${twilio_total:>8.2f}")
    print(f"  OpenAI:  ${oai['total']:>8.2f} (estimated)")
    print(f"  ─────────────────────")
    print(f"  TOTAL:   ${grand_total:>8.2f}")
    print(f"\n  Avg cost per call: ${grand_total / max(len(call_list), 1):.2f}")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    print_report()
