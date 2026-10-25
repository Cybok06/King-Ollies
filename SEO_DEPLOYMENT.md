King Ollies search discovery
============================

Set `PUBLIC_SITE_URL` to the published site's canonical origin (HTTPS, no trailing
path), for example `https://campusdata.store`. That is the current default; change
it if publishing under a new domain. Restart the app after changing the setting.

Public discovery routes:

- `/our-services`: links to every service in the services collection.
- `/our-services/<id>/<name>`: public service description and WhatsApp enquiry.
- `/sitemap.xml`: homepage, directory and database service pages.
- `/robots.txt`: advertises the sitemap.

Service names and images update from the database. Optional `public_description`
text can provide a more detailed customer-facing description for each service.
Internal notes, submitted application data and checker delivery messages are not
used on public service pages. Renamed service URLs redirect to the current name.

After deployment, check the sitemap URLs use the final domain and submit
`https://YOUR-DOMAIN/sitemap.xml` in the verified Google Search Console property.
No Search Console submission or deployment is performed by these code changes.
Sitemap inclusion and structured data help discovery but do not guarantee indexing
or rankings. Keep the public service descriptions accurate and useful.
