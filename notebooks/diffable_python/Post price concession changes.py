# -*- coding: utf-8 -*-
# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: all
#     notebook_metadata_filter: all,-language_info
#     text_representation:
#       extension: .py
#       format_name: light
#       format_version: '1.5'
#       jupytext_version: 1.3.3
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# # Is there a post-price concession impact on prices of drugs in the Drug Tariff?

# We are fully aware of the issues of price concessions and the impact it has on the cost of medicines in England.  The costs are fully quantified by the OpenPrescribing.net Price Concessions tool, which showed that in the 12 months to November 2023 there has been a cost pressure of £275 million, peaking in December 2022 with a cost pressure of £50 million.
#
# However it is not as clear if there is a significant impact on price of medicines once they come of a concession, i.e. are medicines more expensive in the months following a concession, and if so, by how much?
#
# This notebook is a first attempt by the Bennett Institute to prototype an analysis of the issue.
#

import os
import pandas as pd
import numpy as np
#import matplotlib
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
import matplotlib.ticker as ticker
import matplotlib.dates as mdates
# %matplotlib inline
from ebmdatalab import bq
from ebmdatalab import charts

# ### Obtain Price Concession data

# The first thing to do is obtain Price Concession data held on the BI BiqQuery server.

#get price concession data from BigQuery
sql = """
  SELECT DISTINCT
    ncso.vmpp AS vmpp,
    ncso.date AS month,    
    1 AS concession_bool --creates a boolean value to show a price concession exists
  FROM
    ebmdatalab.dmd.ncsoconcession AS ncso --concession table 
"""
exportfile = os.path.join("..","data","ncso_dates.csv") #defines name for cache file
dates_df = bq.cached_read(sql, csv_path=exportfile, use_cache=False) #uses BQ if changed, otherwise csv cache file
dates_df['month'] = pd.to_datetime(dates_df['month']) #ensure dates are in datetimeformat
dates_df = dates_df.sort_values(by=['month','vmpp']) #sort data by month then vmpp
dates_df.head()

# Now we've got the data, we can run an `unstack` which allows us to create a table from 2014 which shows all DT drugs as having either a 0 for no concession, or a 1 for a concession in that month.

#unstacks data, fills missing month data (with zero value where no concession), then restacks
dates_cons_df = dates_df.set_index(['month','vmpp']).unstack().asfreq('MS').fillna(0).stack().sort_index(level=1).reset_index()
#dates_cons_df = dates_cons_df.loc[dates_cons_df['vmpp'] == 1040511000001102]
dates_cons_df.head()

# ### Find the start and end dates of concessions for specific drugs

# Next we can use this table to find the start and end months of a particular concession, and how many months it ran for.

# +
max_date = dates_cons_df["month"].max() + pd.DateOffset(months=-3) #creates variable to ensure that all price concession data have three months after concession ends to ensure calculation of change
pc_summary_df = (dates_cons_df.assign(Consecutive=dates_cons_df.concession_bool
                                .groupby((dates_cons_df.concession_bool != dates_cons_df.concession_bool.shift())
                                    .cumsum()).transform('size')) #creates a value of the number of consecutive months of either price concession or no price concession
          .query('concession_bool > 0') # filters to only where price concession is present 
          .groupby(['vmpp','Consecutive'])
          .aggregate(first_month=('month','first'),  #shows earliest month of consecutive price concession
                     last_month=('month','last')) #shows latest month of consecutive price concession
          .reset_index().query("last_month < @max_date")
          .reset_index(drop=True)
)

pc_summary_df.head()
# -

# ### Find the difference in Drug Tariff costs before and after concessions

# We can now get the Drug Tariff prices from BQ

# +
#get drug tariff price data from BigQuery
sql = """
  SELECT 
    vmpp.bnf_code as bnf_code, --BNF code (at VMP level)
    vmpp.nm as nm, --name
    vmpp.qtyval as unit_qty, --quantity per pack
    dt.*
  FROM
    ebmdatalab.dmd.tariffprice AS dt --concession table
    INNER JOIN
    dmd.vmpp as vmpp --join to VMPP table to get BNF codes and names
    on
    dt.vmpp = vmpp.id
  WHERE
    dt.vmpp IN (SELECT DISTINCT vmpp FROM ebmdatalab.dmd.ncsoconcession)
"""

exportfile = os.path.join("..","data","tariff.csv") #defines name for cache file
dates_df = bq.cached_read(sql, csv_path=exportfile, use_cache=False) #uses BQ if changed, otherwise csv cache file
dates_df['date'] = pd.to_datetime(dates_df['date'])#ensure dates are in datetimeformat
dates_df['unit_qty'] = pd.to_numeric(dates_df['unit_qty'])
# -

# Using the price data, and the table on start and end dates of concessions, we can now calculate the average drug tariff price for the three months _prior_ to the concession starting, and the three months _following_ the end of the concession.

# +
dates_df['pre_month'] = dates_df['date'] + pd.DateOffset(months=1) #creates extra date column in drug tariff price shifted by one month later, to pick up 3 month rolling mean spend for the month before price concession added
dates_df['post_month'] = dates_df['date'] + pd.DateOffset(months=-3) #creates extra date column in drug tariff price shifted by three months earlier, to pick up 3 month rolling mean spend for the 3 months after price concession added
dates_df['3_month_price'] = dates_df.groupby('vmpp')['price_pence'].transform(lambda x: x.rolling(3, 3).mean()) # create three month rolling average drug tariff cost
dates_df_merge = pd.merge(pc_summary_df, dates_df[['bnf_code', 'nm','unit_qty','vmpp','pre_month','3_month_price']],  how='left', left_on=['vmpp','first_month'], right_on = ['vmpp','pre_month']) #merges price concession information with the 3 month average DT price prior to the start of the price concession
dates_df_merge.rename(columns={'3_month_price' : 'pre_pc_price'}, inplace=True) #rename columns
dates_df_merge = pd.merge(dates_df_merge, dates_df[['vmpp','post_month','3_month_price']],  how='left', left_on=['vmpp','last_month'], right_on = ['vmpp','post_month']) #merges price concession information with the 3 month average DT price after the end of the price concession
dates_df_merge.rename(columns={'3_month_price' : 'post_pc_price'}, inplace=True) #rename columns
dates_df_merge = dates_df_merge.drop(columns=['pre_month', 'post_month']) #drop unneccesary columns
dates_df_merge = dates_df_merge.sort_values(by=['vmpp','first_month']) #sort data by month then vmpp
dates_df_merge['perc_difference'] = (dates_df_merge['post_pc_price']/dates_df_merge['pre_pc_price']-1)
dates_df_merge['rx_merge_date'] = (dates_df_merge['last_month'] + pd.DateOffset(months=1)) #create a merge date for prescribing data, so there's always the three months of rx data available post concession
dates_df_merge = dates_df_merge.sort_values(by=['last_month'], ascending=False) #sort data by month then vmpp
dates_df_merge.head()


# -


# ### Get quantity data from BQ

# We can now get the quantity data from the BQ server, to be able to calculate a three month rolling quantity, to allow us to calculate the difference in costs between the start and end of the concessions.

# +
#get quantity_calcs
sql = """
  SELECT DISTINCT
    date(rx.month) as date_3m_start,
    rx.bnf_code,
    SUM(rx.quantity) OVER(
      PARTITION BY rx.bnf_code
      ORDER BY DATE_DIFF(date(rx.month), '2000-01-01', MONTH)
      RANGE BETWEEN 0 PRECEDING AND 2 FOLLOWING
    )
    as roll_3m_quantity
  FROM
    ebmdatalab.hscic.normalised_prescribing AS rx
    INNER JOIN
    dmd.vmpp as vmpp --join to VMPP table to get BNF codes and names
    on
    rx.bnf_code = vmpp.bnf_code
  WHERE
    vmpp.id IN (SELECT DISTINCT vmpp FROM ebmdatalab.dmd.ncsoconcession)
    AND month >='2022-04-01'
    ORDER BY date_3m_start DESC
"""

exportfile = os.path.join("..","data","rx_qty.csv") #defines name for cache file
rx_df = bq.cached_read(sql, csv_path=exportfile, use_cache=True) #uses BQ if changed, otherwise csv cache file
rx_df['date_3m_start'] = pd.to_datetime(rx_df['date_3m_start'])#ensure dates are in datetimeformat
rx_df = rx_df[rx_df['date_3m_start'] <= max(rx_df['date_3m_start']) + pd.DateOffset(months=-2)] #limit df to ensure that always 3 full months of data
rx_df.head()
# -

# ### Calculate impact

# Join the two datasets together to be able to calculate costs

rx_df_merge = pd.merge(dates_df_merge, rx_df,  how='right', left_on=['bnf_code','rx_merge_date'], right_on = ['bnf_code','date_3m_start']) #merge quantity and DT dfs
rx_df_merge['3_m_additional_cost'] = 0.01*(rx_df_merge['roll_3m_quantity']/rx_df_merge['unit_qty'])*(rx_df_merge['post_pc_price']-rx_df_merge['pre_pc_price']) # calculate additional costs
exportfile = os.path.join("..","data","3_months_post.csv") #defines name for cache file
rx_df_merge.to_csv(exportfile, index=False)  
rx_df_merge.head()

# Finally, we can calculate the additional three month costs for any items no longer on concession in the previous month.

rx_sum_df = rx_df_merge.groupby('date_3m_start')['3_m_additional_cost'].sum().apply(lambda x: "£{:,.2f}".format(x)).to_frame()
rx_sum_df.head(24)

# We can see from the above analysis that there appears to be a significant impact in the costs of medicines in the three months following the concession, compared with the three months prior to the concession. In the 16 months since April 2022 for which the data is available, the cost impact is approximately £145 million.  Further work should be undertaken.


