# CallPilot Clients

Each subfolder represents one client. The AI reads the client's documents automatically via RAG.

## Structure

```
clients/
├── default/               ← Default client (Raju)
│   ├── profile.txt        ← REQUIRED: Full Name, phone, address, account info
│   ├── system-prompt.txt  ← OPTIONAL: Override global system prompt for this client
│   └── *.txt/pdf/md       ← Any documents (insurance card, medical info, etc.)
├── sarah/
│   ├── profile.txt
│   └── insurance.txt
└── john/
    └── profile.txt
```

## profile.txt Format

The AI extracts the client's name from the `Full Name:` line:

```
Full Name: Jane Smith
Phone: (512) 555-1234
Address: 123 Main St, Austin, TX 78701
Insurance Provider: Blue Cross PPO
Member ID: XYZ123456
```

## Adding a New Client

1. Create `clients/{client_id}/` folder
2. Add `profile.txt` with at least `Full Name:` line
3. Add any relevant documents (.txt, .pdf, .docx, .md)
4. Restart the server (auto-indexes on startup)
5. Pass `client_id` in the call request

## Switching Clients Per Call

In the UI, set the **Client ID** field to your client's folder name.
Via API: `POST /call` with `{"client_id": "sarah", ...}`
