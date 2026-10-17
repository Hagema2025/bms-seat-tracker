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
        
        // 1. Force the exact User-Agent we will use in Python
        await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

        // Establish Chennai region context
        await page.goto('https://in.bookmyshow.com/explore/home/chennai', { 
            waitUntil: 'networkidle2', 
            timeout: 30000 
        });

        // Navigate to the specific layout
        const layoutUrl = `https://in.bookmyshow.com/movies/chen/seat-layout/${eventCode}/${venueCode}/${sessionId}/${dateStr}`;
        await page.goto(layoutUrl, { 
            waitUntil: 'networkidle2', 
            timeout: 30000 
        });

        // 2. CRITICAL: Wait 8 seconds for Cloudflare Turnstile to finish and set cookies
        await new Promise(r => setTimeout(r, 8000));

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
