# Tableau Personal Access Token (PAT) Connection Guide

## Overview

The Workbook Compare App uses **Personal Access Tokens (PATs)** to authenticate with your Tableau Server. This guide walks you through creating a PAT on Tableau Server and configuring it in the app.

---

## What is a Personal Access Token?

A **Personal Access Token (PAT)** is a credential pair consisting of:
- **Token Name**: A user-friendly identifier (e.g., `workbook-compare-app`)
- **Token Secret**: A secure token string used for authentication

PATs are:
- ✅ Scoped to a specific user account (the creator)
- ✅ Can be revoked individually without changing the user's password
- ✅ More secure than embedding passwords in configuration
- ✅ Can have restricted permissions based on Tableau Server's capability settings
- ✅ Never stored as plaintext in the database (encrypted with Fernet)

---

## Step 1: Create a Personal Access Token on Tableau Server

### Prerequisites
- You have a Tableau Server account with appropriate permissions
- You can access the Tableau Server UI as an admin or authorized user
- The account will be used for all workbook compare operations

### Creating the PAT

**1. Log in to Tableau Server**
   - Navigate to your Tableau Server (e.g., `https://your-tableau-server.com`)
   - Sign in with your account credentials

**2. Go to Account Settings**
   - Click your **Profile Icon** (top-right corner)
   - Select **Settings** → **Account** (or **My Profile**)

**3. Create a New Token**
   - Look for **Personal Access Tokens** section
   - Click **New Token** or **Create New Token**
   - Enter a descriptive name:
     ```
     workbook-compare-app
     ```
   - Optionally, set an expiration date (recommended for security):
     - 90 days: Good balance between security and convenience
     - 1 year: Longer-lived token, less frequent updates

**4. Copy and Save the Token Secret**
   - Tableau will display the token secret **once and only once**
   - Copy it to a temporary secure location (e.g., a password manager, encrypted file)
   - ⚠️ **Important**: You cannot retrieve this secret again—if lost, you must delete and recreate the token

   Example secret format:
   ```
   abcd1234efgh5678ijkl90mn
   ```

**5. Verify Token Permissions**
   - Tableau Server admins can restrict PAT capabilities
   - Ensure your token can:
     - `List workbooks` (required)
     - `Download/Export workbooks` (required)
     - `Query custom views` (required for Custom View analysis)
   - If restricted, contact your Tableau Admin to enable these capabilities for your PAT

---

## Step 2: Configure the App with Your PAT

### Initial Setup

When you first run the Workbook Compare App, it will show a **Setup** page:

```
http://localhost:8000/setup
```

**Fill in the form:**

| Field | Value |
|-------|-------|
| **Server URL** | `https://your-tableau-server.com` |
| **Tableau Site** | Your site name (default: leave blank for the default site) |
| **PAT Name** | The token name you created (e.g., `workbook-compare-app`) |
| **PAT Secret** | The token secret (the long alphanumeric string) |
| **Passcode** | A local admin passcode for the app (e.g., `MySecurePasscode123`) |

### Example Setup

```
Server URL:  https://tableau.mayoclinic.org
Site Name:   analytics
PAT Name:    workbook-compare-app
PAT Secret:  abcd1234efgh5678ijkl90mn
Passcode:    MySecurePasscode123
```

### What Happens During Setup

1. **Validation**: The app connects to Tableau Server using your PAT
   - If the connection fails, you'll see an error message
   - Check the PAT name, secret, and server URL
   - Ensure the Tableau Server is reachable

2. **Encryption**: Your PAT secret is encrypted before storing
   - Uses Fernet encryption (symmetric, secure)
   - Only the passcode is stored as a hash (irreversible)
   - Never logged or displayed in plaintext

3. **Success**: You'll be directed to log in with your passcode

---

## Step 3: Log In to the App

After setup, the app requires authentication:

**1. Go to the Login Page**
   ```
   http://localhost:8000/login
   ```

**2. Enter Your Passcode**
   - Enter the passcode you created during setup
   - Click **Sign In**

**3. Access the App**
   - You're now authenticated and can use Workbook Compare

---

## Step 4: Verify the Connection

Once logged in, verify your PAT connection is working:

### Check 1: Workbook List
1. Go to **Workbook Compare** page
2. Click **Select Workbook**
3. You should see a live list of workbooks from your Tableau Server
4. If the list is empty or shows an error, check:
   - PAT has read permissions for workbooks
   - You have access to workbooks on the selected site

### Check 2: Download a Workbook
1. Select a workbook from the list
2. Click **Download**
3. The app should download the workbook definition
4. If this fails, check:
   - PAT has download/export permissions
   - The workbook is not locked or in use

### Check 3: View Custom Views (if available)
1. After a successful comparison, scroll to the **Custom Views** section
2. You should see custom views from the workbook
3. If this section is empty, the workbook may not have any custom views

---

## Troubleshooting

### "Could not sign in to Tableau Server"
**Causes:**
- Incorrect PAT name or secret
- PAT is expired or revoked
- Server URL is wrong or unreachable

**Solutions:**
1. Verify the PAT name and secret in your Tableau Server settings
2. Check if the PAT has an expiration date that has passed
3. Create a new PAT if the old one is expired
4. Test server connectivity:
   ```bash
   curl https://your-tableau-server.com/api/2.4/serverinfo
   ```

### "Server Unreachable" After Setup
**Causes:**
- Tableau Server is down or unreachable
- Network/firewall blocking access
- SSL certificate issues (self-signed certs)
- Proxy or VPN issues

**Solutions:**
1. Verify the server is running:
   ```bash
   ping your-tableau-server.com
   ```
2. Test REST API access:
   ```bash
   curl https://your-tableau-server.com/api/2.4/serverinfo
   ```
3. If using a self-signed SSL certificate, ensure the app trusts it
4. Check your network/firewall rules
5. Contact your Tableau Admin if issues persist

### Workbook List is Empty
**Causes:**
- No workbooks on your site
- Insufficient permissions (PAT can't list workbooks)
- Wrong site selected

**Solutions:**
1. Verify you have workbooks on the selected site
2. Check if your Tableau account has view permissions
3. Contact your Tableau Admin to enable PAT capabilities for workbook listing

### Cannot Download Workbook
**Causes:**
- PAT lacks download/export permissions
- Workbook is locked or being edited
- Extract content is restricted

**Solutions:**
1. Verify your PAT has "ExportWorkbook" capability enabled
2. Ensure the workbook is not locked
3. Contact your Tableau Admin if capabilities need adjustment

### "Passcode Required" on Every Request
**Causes:**
- Session expired (default: 24 hours)
- Cookie issues (browser clearing cookies)

**Solutions:**
1. Re-enter your passcode
2. Clear browser cache and cookies for the app
3. Use private/incognito browsing mode (disables most cookie restrictions)

---

## Security Best Practices

### 1. Protect Your PAT Secret
- ❌ Do NOT share your PAT secret
- ❌ Do NOT commit PAT secrets to version control
- ❌ Do NOT share the token with unauthorized users
- ✅ Store in a secure password manager
- ✅ Keep backup in encrypted storage

### 2. Use Token Expiration
- Set PAT expiration to **90 days** (recommended)
- Create a calendar reminder to renew before expiration
- After rotation, the old PAT becomes invalid

### 3. Monitor Token Usage
- Periodically check Tableau Server audit logs
- Revoke PATs that are no longer needed
- Use separate PATs for different applications (if creating multiple)

### 4. Rotate Tokens Regularly
- Create a new PAT every **90-180 days**
- Delete the old PAT after confirming the new one works
- This limits exposure if a token is compromised

### 5. Restrict Token Scope (If Possible)
- Ask your Tableau Admin to restrict PAT capabilities
- Your token should only have access to:
  - `Read workbooks`
  - `Download workbooks`
  - `Query custom views` (if using Custom View analysis)
- Avoid giving unnecessary permissions

---

## Reconfiguring the App

### Updating to a New PAT

If your PAT expires or you need to rotate it:

**1. Create a new PAT on Tableau Server** (follow Step 1 above)

**2. Access Setup Page**
   - Clear your app data or delete `instance/app.db`
   - Or manually delete the config: go to the database and clear `pat_name` and `pat_encrypted`

**3. Go to Setup URL**
   ```
   http://localhost:8000/setup
   ```

**4. Fill in the New PAT Details** (Step 2 above)

**5. Delete the Old PAT on Tableau Server**
   - Go to your Tableau Server account settings
   - Find the old PAT in **Personal Access Tokens**
   - Click **Revoke**

---

## FAQ

### Q: Can I use the same PAT for multiple applications?
**A:** Yes, but it's less secure. Each application using the same PAT can access the same resources. For better security, create separate PATs for each application.

### Q: What happens if I delete the PAT on Tableau Server?
**A:** The app will lose access immediately. You'll get "Could not sign in" errors on the next request. Create a new PAT and reconfigure the app.

### Q: Can I use a password instead of a PAT?
**A:** No. Tableau Server's REST API (which this app uses) requires PAT authentication. Username/password is not supported for programmatic access.

### Q: How long does a PAT last?
**A:** Until you set an expiration date (optional) or manually revoke it. Best practice: set expiration to 90 days.

### Q: Is my PAT secret visible in the app?
**A:** No. Secrets are encrypted immediately after setup and never displayed, even in settings. Only the PAT **name** is stored as plaintext.

### Q: Can I retrieve my PAT secret if I forget it?
**A:** No. Tableau Server only displays the secret once. If you lose it, you must delete and recreate the PAT.

### Q: What if my PAT has limited permissions?
**A:** Some operations will fail with permission errors. The app will display helpful error messages. Ask your Tableau Admin to grant the required capabilities.

---

## Verification Checklist

After setup, verify everything is working:

- [ ] PAT created on Tableau Server
- [ ] PAT secret saved securely
- [ ] App setup completed without errors
- [ ] App login successful with passcode
- [ ] Workbook list displays workbooks
- [ ] Can select and download a workbook
- [ ] Custom views appear (if workbook has any)
- [ ] No "Server Unreachable" or "Not Signed In" errors

If all items are checked, your PAT connection is working correctly!

---

## Next Steps

Once your PAT is configured:
1. Upload a workbook to Workbook Compare
2. Select a candidate version to compare
3. Review the impact analysis and custom view changes
4. Use the live state verification features for deeper analysis

For more details, see [LIVE_VERIFICATION_GUIDE.md](LIVE_VERIFICATION_GUIDE.md).
