const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
puppeteer.use(StealthPlugin());

const args = process.argv.slice(2);
const eventCode = args[0];
const venueCode = args[1];
const sessionId = args[2];
const dateStr = args[3];

(async () => {
    // Launch stealth browser
    const browser = await puppeteer.launch({
        headless: 'new', // Use the new headless mode
        args: [
            '--no-sandbox', 
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled'
        ]
    });

    try {
        const page = await browser.newPage();
        
        // Establish Chennai region context
        await page.goto('https://in.bookmyshow.com/explore/home/chennai', { 
            waitUntil: 'networkidle2', 
            timeout: 30000 
        });

        // Navigate to the specific layout to trigger any specific session cookies
        const layoutUrl = `https://in.bookmyshow.com/movies/chen/seat-layout/${eventCode}/${venueCode}/${sessionId}/${dateStr}`;
        await page.goto(layoutUrl, { 
            waitUntil: 'networkidle2', 
            timeout: 30000 
        });

        // Extract cookies
        const cookies = await page.cookies();
        const cfClearance = cookies.find(c => c.name === 'cf_clearance')?.value || "";
        const bmsId = cookies.find(c => c.name === 'bmsId')?.value || "";

        const tokens = {
            "x-token": "67x1xa33b4x422b361ba", // Maintained from your original logic
            "cf_clearance": cfClearance,
            "bmsId": bmsId
        };

        // Output strictly as JSON so Python can parse it
        console.log(JSON.stringify(tokens));

    } catch (error) {
        console.error(JSON.stringify({ error: error.message }));
    } finally {
        await browser.close();
    }
})();
