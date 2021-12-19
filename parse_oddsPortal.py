import re
import pandas as pd
import numpy as np
from datetime import timedelta
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as ec


def get_game_info(driver):
    """
        Returns basic information on the game
    """
    game_header = driver.find_element(By.ID, 'col-content').text.split('\n')[:3]
    
    home_team, away_team = game_header[0].split(' - ')
    
    resp =  {
        'Game_URL': driver.current_url,
        'Home_Team': home_team,
        'Away_Team': away_team,
        'Datetime': pd.to_datetime(game_header[1]) - timedelta(hours=3),
        'Final_Result': game_header[2].split()[2],
        '1st_Half_Result': game_header[2].split()[3][1:-1],
        '2nd_Half_Result': game_header[2].split()[4][0:-1]
    }
    
    homeFull, awayFull = resp['Final_Result'].split(':')
    home1st, away1st = resp['1st_Half_Result'].split(':')
    home2nd, away2nd = resp['2nd_Half_Result'].split(':')
    
    resp.update({
        'Home_Score_FullTime': int(homeFull),
        'Home_Score_1st_Half': int(home1st),
        'Home_Score_2nd_Half': int(home2nd),
        'Away_Score_FullTime': int(awayFull),
        'Away_Score_1st_Half': int(away1st),
        'Away_Score_2nd_Half': int(away2nd),
    })    
    
    return resp 



def parse_handicaps(table_text, col_names):
    """
        Parses data into DataFrame for the following types of bet:
            - AH (Asian Handicap)
            - O/U (Over/Under)
            - EH (European Handicap)
            - CS (Correct Score)
            - HT-FT (Half Time/Full Time)
    """
    # Split each line of the table related
    # to the 'compare odds' string
    table_text = table_text.replace('(1)', '').replace('(0)', '').replace('(2)', '')
    clean_str = re.split('Click\sto\sshow', table_text)[0]
    clean_str = re.split('Average', clean_str)[0]
    clean_str = re.split('Hide\sodds', clean_str)[0]
    
    str_split = clean_str.split('Compare odds')
    
    df = pd.DataFrame([x.strip().split('\n') for x in str_split])
    # Remove last line and last column
    if len(df) > 1:
        df = df[:-1] 
    if len(df.columns) - 1 == len(col_names):
        df = df[df.columns[:-1]]
    
    df.columns = col_names
    df = df.dropna(axis=0) # Remove empty lines 
    
    # cast payout column to float
    if 'Payout' in df.columns:
        df['Payout'] = df['Payout'].str.replace('%', '').astype(float)/100

    return df  

def parse_odds(table_text):
    """
        Parses data into DataFrame for the following types of bet:
            - 1x2
            - Home/Away
            - DNB (Draw No Bet)
            - Double Chance
            - Odd or Even
            - Both Teams to Score
    """
    # Works for 1x2, Home/Away bet-types
    
    # Cleans string only until 'Click to show' or 'Average' message
    clean_str = re.split('Click\sto\sshow', table_text)[0]
    clean_str = re.split('Average', clean_str)[0].split('\n ')
    
    
    # Splits each line and convert it to a matrix 
    table_str = []
    for i, x in enumerate(clean_str):
        table_str.append(x.split('\n') if i > 0 else x.split())
    
    df = pd.DataFrame(table_str).replace('-', np.nan)
    df.columns = df.iloc[0] # first line as header
    df = df[1:][df.columns[:-1]] #Remove first line and last column
    
    # cast payout column to float
    if 'Payout' in df.columns:
        df['Payout'] = df['Payout'].str.replace('%', '').astype(float)/100
        
    return df


def get_bet_type(driver):
    """
        Returns the bet type of the current page
    """
    # Takes the type of the bet
    try:
        bet_type = driver.current_url.split('#')[1].split(';')[0]
    except:
        # In case it is not in the URL, 1X2 is the default
        bet_type = '1X2'
        
    return bet_type


def get_elements_tempo(driver):
    subactives = driver.find_elements(By.CLASS_NAME, 'subactive')
    
    for x in subactives:
        if '1st Half' in x.text or 'Full Time' in x.text:
            break
        
    return x.find_elements(By.TAG_NAME, 'li')


def get_df(driver):
    driver_text = driver.find_element(By.CLASS_NAME, 'table-main').text
    
    bet_type = get_bet_type(driver)
    
    # Indicates that the data is in text format, not HTML table
    # use the method 'parse_handicaps'
    if driver_text == '':
        # Special header for these types of bets:
        if bet_type == 'ht-ft':
            cols = ['Result', 'Odds']
        elif bet_type == 'cs':
            cols = ['Score', 'Odds']
        else:
            # default header from HTML
            cols = driver.find_element(By.CLASS_NAME, 'table-chunk-header-dark').text.split()
            
        resp =  parse_handicaps(
            driver.find_element(By.ID, 'odds-data-table').text, 
            cols
        )
    else:
        # Data comes in table, using the method 'parse_odds'
        resp = parse_odds(driver.find_element(By.ID, 'odds-data-table').text)
        
        # Post processing for the Odd/Even bet type
        if bet_type == 'odd-even':
            resp = resp.dropna(axis=1, thresh=1) # remove empty columns
            resp = resp.loc[:, ~resp.columns.duplicated()] # remove duplicated columns
            resp['Payout'] = resp['Goals'].str.replace('%', '').astype(float)/100
            
            # remove unnecessary columns
            del resp['Goals']
            del resp['Bookmakers']
            
            # renaming columns
            resp.columns = ['Bookmakers', 'Odd', 'Even', 'Payout']
            
    map_replaces = {
        '1': 'Home',
        '2': 'Away',
        'X': 'Draw'
    }

    resp.columns = [map_replaces[x] if x in map_replaces else x for x in resp.columns]

    return resp


def get_info_as_jsons(df, bet_type, game_period):
    """
        Parses DataFrame info into a single json
    """
    # Get dataframe as dictionary
    dic = df.set_index(df.columns[0]).to_dict('index')
    
    #Serializes and adds suffix
    r = {}
    for key in dic:       
        # When there are bookmakers options, add into prefix
        if 'Bookmakers' in df.columns: 
            bookmaker = key.strip()
            
            # For example:
                #  1x2__(bet365): Home__1stHalf
            r.update(
                {bet_type + "__(" + bookmaker + ")-> " + k + "__" + game_period : dic[key][k] for k in dic[key]}
            )
        elif bet_type == 'cs':
            bet_subtype = key.strip()
            # For example:
                #  Correct_Score__1:0: Home__1stHalf
            r.update(
                {'Correct_Score__' + bet_subtype + "-> " + k + "__" + game_period: dic[key][k] for k in dic[key]}
            )
        else:
            bet_subtype = key.strip()
            
            # For example:
                #  1x2__(bet365): Home__1stHalf
            r.update(
                {bet_subtype + "-> " + k + "__" + game_period: dic[key][k] for k in dic[key]}
            )
    
    return r


def get_all_dfs(driver, verbose=True, pass_exception=False):
    # Iterate through types of bets:
    tabs_bet_types = driver.find_element(By.CLASS_NAME, 'ul-nav').find_elements(By.TAG_NAME, 'li')
    
    resp = get_game_info(driver)

    for tab in tabs_bet_types:
        try:
            tab.click()
        except Exception as e:
            if tab.text == '':
                continue
            else:
                raise e

        # Takes the type of the bet
        bet_type = get_bet_type(driver)

        if verbose:
            print(f"Current page: {tab.text} <-> {bet_type}")

        li_halfs = get_elements_tempo(driver)
        for i, el in enumerate(li_halfs):
            el.click()
            # time.sleep(3)
            
            wait = WebDriverWait(driver, 30)
            # x = wait.until(lambda x: x.find_element(By.CLASS_NAME, "table-main"))
            wait.until(ec.visibility_of_element_located((By.ID, 'odds-data-table')))
            
            current_game_period = el.text
            
            if verbose:
                print(f"\t{current_game_period}")
                
            retries = 3
            while(retries >= 0):
                try:
                    r = get_info_as_jsons(
                        get_df(driver),
                        bet_type, 
                        current_game_period
                    )
                    break
                except Exception as e: 
                    retries -= 1
                    if retries < 0:
                        if pass_exception:
                            r = {}
                            break
                        else:
                            raise e
                    if verbose:
                        print(f"Retrying... {retries}")
                        
            resp.update(r)
            
    return resp


def get_game_links_page(driver):
    links_in_table = driver.find_element(By.ID, 'tournamentTable').find_elements(By.TAG_NAME, 'a')
    
    r = []
    for x in links_in_table:
        if '-' in x.text:
            link = x.get_attribute('href')
            
            r.append({
                'Game': x.text,
                'Link': link,
                'Sport': link.split('/')[3],
                'Country': link.split('/')[4],
                'League': link.split('/')[5],
            })
    return r


def get_game_links(driver, sport='soccer', country='brazil', league='serie-a-2016'):
    page_i = 1

    resp = []

    while(True):
        print(f"Acessing page {page_i}...", end="\r")

        driver.get(f"https://www.oddsportal.com/{sport}/{country}/{league}/results/#/page/{page_i}/")

        wait = WebDriverWait(driver, 20)
        wait.until(ec.visibility_of_element_located((By.ID, 'tournamentTable')))

        text = driver.find_element(By.ID, 'tournamentTable').text

        if 'Unfortunately, no matches can be displayed' in text:
            break

        resp.extend(
            get_game_links_page(driver)
        )

        page_i += 1
    
    return resp