ELAI_AI — Midnight Glass Theme Build (Corrected Workflow)
========================================================

WHAT'S INCLUDED
- Midnight/glass themed marketplace and IvoryBlock admin console.
- Existing seller, buyer and admin accounts are preserved.
- Every new seller listing is screened before it can appear in the marketplace.
- Safe listings are published automatically.
- Suspicious listings are held in the Admin Review Queue and hidden from buyers until an admin approves them.
- Critical listings are automatically blocked. Critical cases cannot be approved from the normal review controls.
- Sellers can see the current status and reason for their listings.
- Buyers can open My reports to see each reported product, their report reason, current status, admin response, and final action.
- Admins can respond to individual buyer reports and record whether a violation was confirmed or the listing was retained.
- Report decisions and responses are stored in SQLite and persist across logouts/restarts.
- Existing AI Analyzer, Batch Scan, Model Lab, Dataset & Training, Case Files, seller risk, and demo controls remain available.

RUN
1. Extract the ZIP to a normal folder.
2. Double-click run1.bat, or open Command Prompt in the extracted folder and run: python app.py
3. Open http://127.0.0.1:8000 if it does not open automatically.
4. Use the demo account buttons on the login screen, or use the accounts below.

DEMO ACCOUNTS
Role            Username       Password
Admin           admin          admin123
Buyer           buyer1         buyer123
Buyer           buyer2         buyer123
Seller          seller1        seller123
Seller          craft_house    seller123
Seller          green_grove    seller123
Seller          artisan_lane   seller123
Seller          rare_finds     seller123

TESTING SUGGESTIONS
- Safe: "Wildlife photography poster" with a description about conservation photography/educational prints.
- Suspicious: "Rare horn pieces" with bulk, private-channel, crypto-only, or discreet-shipping wording. It should be held for admin review.
- Critical: "Rhino horn powder" or "Pangolin scales dried" offered for sale. It should be blocked.
- For the suspicious listing, log in as admin and approve/reject it in Review Queue. Only approval publishes it.
- Log in as a buyer, report a published product, then use My reports to follow the admin's response.

NOTES
- Demo data is created automatically on first run. SQLite data is saved in ivoryblock.db in this folder.
- The AI screening is a prototype for academic demonstration, not a substitute for expert/legal review. Text classification can make mistakes.
- Demo credentials are intentionally simple and must not be used for a production deployment.
