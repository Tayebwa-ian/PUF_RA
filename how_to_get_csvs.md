## IEEE
Issue: Can only download first 1000 unselected results and then have to page by page (100 items per page max) download CSVs

Solution:
- Execute query
- Sort by release date (oldest to newest)
- Download first 1000 unselected one's through the export function
- Adjust date so that next 1000 one's are included

Then combine the files using the command `awk '!seen[$0]++' file1.txt file2.txt file3.txt > merged.txt`


## ACM
- Execute query on ACM page, append `ContentItemType=research-article` to URL
- Select some on paper on page, click "Export citations", then select "All Results" and BibTeX (which is the only one that includes abstracts)
    - Sometimes, this does not export the right format, reloading helped then