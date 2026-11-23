# Jobs feed for the Development Jobs Atlas

A free robot that runs once a day on GitHub. It reads public job feeds, matches each job to a city and country, and publishes one file, `jobs.json`, that the jobs map on khounkham.org reads.

- **Sources (no registration needed):** UN Careers RSS (all UN Secretariat openings, including UNEP, OCHA, UN-Habitat and the regional commissions) and ReliefWeb Jobs RSS (UN agencies, NGOs, Red Cross; UNICEF posts its jobs there too).
- **Places:** GeoNames gazetteer, built in (CC BY 4.0).
- **Cost:** free. Public GitHub repositories get Actions and Pages at no charge.

## One-time setup (about 20 minutes)

1. **Create a GitHub account** at github.com if you don't have one.
2. **Create a repository:** click **+ → New repository**, name it `jobs-feed`, choose **Public**, tick **Add a README**, then **Create repository**.
3. **Upload the files:** on the repository page click **Add file → Upload files**. Drag in everything from this folder: `collect.py`, `requirements.txt`, the `docs` folder and the `.github` folder. Then click **Commit changes**.
   - The `.github` folder is hidden on some computers. If you can't see it, create the file in GitHub instead: **Add file → Create new file**, type the name `.github/workflows/update-jobs.yml`, paste in the contents of that file, and commit.
4. **Allow the robot to save its results:** go to **Settings → Actions → General → Workflow permissions**, choose **Read and write permissions**, then **Save**.
5. **Turn on the website:** go to **Settings → Pages**. Under **Build and deployment**, choose **Deploy from a branch**, branch **main**, folder **/docs**, then **Save**.
6. **Run it the first time:** open the **Actions** tab, click **Update jobs → Run workflow**. It takes a few minutes. A green tick means success.
7. **Copy your feed address.** It will look like this:
   `https://YOUR-GITHUB-NAME.github.io/jobs-feed/jobs.json`
   Opening `https://YOUR-GITHUB-NAME.github.io/jobs-feed/` shows how many jobs it found and when it last ran.
8. **Connect the map:** send the address to Claude, or paste it into the map page yourself. Find this line and put the address between the quotes:
   `const JOBS_URL = "";`

After that it refreshes every day at 04:17 UTC on its own.

## Optional: switch to the ReliefWeb API later

If ReliefWeb approves your appname, the robot can use their API for complete, structured data. Go to **Settings → Secrets and variables → Actions → New repository secret**, name it `RW_APPNAME`, paste the appname, and save. No code changes are needed.

## If something goes wrong

- **Red cross in Actions:** open the failed run to see which feed failed. If one source is down, the robot still saves jobs from the others. If every source fails, it keeps yesterday's file.
- **Few ReliefWeb jobs:** ReliefWeb may limit how many items its RSS feeds return. The robot adds one search feed per country in `COUNTRY_SEARCHES` (in `collect.py`), which you can extend.
- **Runs stop:** GitHub can pause scheduled runs on repositories it considers inactive and sends you an email. Re-enable them in the **Actions** tab.
- **Job data credits:** UN Careers (careers.un.org) and ReliefWeb (reliefweb.int, UN OCHA). Each job links to its original posting, and candidates apply there.
