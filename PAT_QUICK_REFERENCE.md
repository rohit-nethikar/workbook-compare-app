# Tableau PAT Quick Reference Card

## 30-Second Setup

### On Tableau Server
1. Account Settings → Personal Access Tokens
2. Click "New Token" → Name: `workbook-compare-app`
3. Set expiration: 90 days
4. **Copy the secret** (shown once!)

### In the App
1. Go to `http://localhost:8000/setup`
2. Fill in:
   - **PAT Name**: `workbook-compare-app`
   - **PAT Secret**: (paste the secret from step above)
   - **Passcode**: Create your app login password
3. Click "Save & Sign In"
4. Log in with your passcode

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Could not sign in" | Check PAT name/secret; verify Tableau Server URL |
| Empty workbook list | Verify PAT read permissions; check you have workbooks on the site |
| "Server unreachable" | Test: `curl https://your-server.com/api/2.4/serverinfo` |
| Passcode not working | Restart the app; clear browser cookies |

---

## Key Points

✅ **Security**
- Never share your PAT secret
- Revoke old tokens after rotating
- Set 90-day expiration

✅ **Permissions Needed**
- List workbooks
- Download workbooks  
- Query custom views

✅ **If PAT Expires**
- Create new PAT on Tableau Server
- Update app with new secret
- Delete old PAT

---

## Full Documentation

See [PAT_CONNECTION_GUIDE.md](PAT_CONNECTION_GUIDE.md) for:
- Step-by-step creation guide
- Detailed troubleshooting
- Security best practices
- FAQ
