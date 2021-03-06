"""
Functions to download and display stats about WXPN's A-Z songs playlist

Author: Lena Bartell, github.com/lbartell
"""

# imports
import re
import pandas as pd
import os
import string
import musicbrainzngs as mb
import datetime as dt
import glob
import numpy as np
import sys
import unidecode
pd.options.mode.chained_assignment = None  # default='warn'

# =========================================================================== #
# function definitions
# =========================================================================== #

def read_playlist_data(filename=None, rawbasename=None, updateMB=False, savedata=True,
                       resetMB=False):
    '''
    Import data saved by the scrapy spider, update the musicbrainz data,
    re-save the data, and return the data as a pandas DataFrame

    Args:
        filename (str): path to csv file with current playlist data, if None
            use default 'playlistdata.csv'

        rawbasename (str): path to csv files with raw/scraped playlist data,
            use wildcards to define path format, string is fed to glob for
            pattern matching, if None use default
            './scrape_playlist/playlistdata_*.csv'

        updateMB (bool): if True, connect to and update release year data from
            the MusicBrainz database. Default is False. Note, only entries with
            a release year of 0 will be searched for and updated. If no results
            are found in MB, then set year to 1 (see below)

        savedata (bool): if True, save combined data to CSV. Default is True

        resetMB (bool): if True, set all release year values to 0 (see below)

        Release year key
            if 0: haven't looked for this track yet (new data)
            if 1: failed to find this track before (don't try again)
    '''

    # setup for import
    col_dtypes = {'artist':str, 'track':str, 'time':dt.datetime,
                  'release_year':np.int64, 'sec_diff':np.float}
    csv_kwargs = {'sep': '\t', 'header': 0, 'dtype': col_dtypes,
                  'index_col':'time'}

    # import all raw data & remove duplicates
    if rawbasename is None:
        rawbasename = './scrape_playlist/playlistdata_*.csv'
    paths = glob.glob(rawbasename)
    dflist = []
    for path in paths:
        dflist.append(pd.read_csv(path, **csv_kwargs))
    raw_data = pd.concat(dflist)
    raw_data.index = pd.to_datetime(raw_data.index)
    raw_data['time'] = raw_data.index
    raw_data = raw_data.drop_duplicates(subset='time')
    #raw_data = raw_data.drop_duplicates().reset_index()

    # import all current data & remove duplicates
    if filename is None:
        filename = 'playlistdata.csv'

    filename = os.path.abspath(filename)
    if os.path.isfile(filename):
        # import prevous data
        prev_data = pd.read_csv(filename, **csv_kwargs)
        prev_data.index = pd.to_datetime(prev_data.index)
        prev_data['time'] = prev_data.index
        prev_data = prev_data.drop_duplicates(subset='time')

        #prev_data = prev_data.drop_duplicates().reset_index()

        # combine into master dataframe
        cols_to_keep = ['release_year', 'album', 'artist', 'track', 'sec_diff', 'time']
        data = prev_data.merge(raw_data, how='outer')
        data = data.drop_duplicates(subset='time')
        data = data[cols_to_keep]
        data = data.reset_index()

    else:
        # set raw data as current data
        data = raw_data

    # fill NaNs with zeros
    if ('release_year' not in data.columns) or resetMB:
        data['release_year'] = np.int64(0)

    data = data.fillna(value=int(0))
    data['release_year'] = data['release_year'].astype(np.int64)

    # save data
    cols_to_write = ['release_year', 'album', 'artist', 'track', 'sec_diff', 'time']
    to_csv_kwargs = {'sep':'\t', 'encoding':'utf-8', 'index':False, 'columns':cols_to_write}
    if savedata:
        data.to_csv(filename, **to_csv_kwargs)
        print 'data - combined raw and prev data and saved to: {0}'.format(filename)

    # update musicbrainz data
    if (not data.empty) and updateMB:
        #letters = list(string.ascii_lowercase)
        letters = list(set([x[0] for x in data.track]))

        # update one letter at a time, saving in between
        for letter in letters:

            print letter

            # get MB data for the current subset
            keep = [x[0].lower()==letter for x in data['track']]
            sub_data = data.loc[keep]
            sub_data = get_mb_data(sub_data)

            # append it to the full dataset and save
            data[keep] = sub_data
            data.to_csv(filename, **to_csv_kwargs)
            print 'data - update MB data for {0} and saved to: {1}'.format(letter, filename)

    # add elapsed time column
    if not data.empty:
        data = data.sort_values('time')
        times =  pd.to_datetime(data['time'])
        c = [(y-x) for (x,y) in zip(times[:-1], times[1:])]
        c.append(times[0]-times[0])
        secs = [x.total_seconds() for x in c]
        secs_fixed = []
        maxdt = 30.*60. # if a song was longer than 30 mins, dont' trust the time - just set to 1 second instead
        for s in secs:
            if s <= maxdt:
                secs_fixed.append(s)
            elif s > maxdt:
                secs_fixed.append(1.)

        data['sec_diff'] = secs_fixed
        if savedata:
            data.to_csv(filename, **to_csv_kwargs)
            print 'data - sec_diff added and saved to: {0}'.format(filename)

    return data


def get_mb_data(data):
    '''
    Given pandas dataframe containing song information, fill in data about
    release year, album, etc, for entires that don't yet have that info. Return
    the completed table
    '''

    # setup musicbrainz agent
    filename = 'contact'
    filename = os.path.abspath(filename)
    f = open(filename, 'r')
    contact = f.read()
    f.close()
    mb.set_useragent('xpn playlist analysis', '1.0', contact=contact)

    # setup output
    years = []
    albums = []

    # run search on each song
    for song in data.itertuples():

        # only run the search if we don't have the data already
        if (song.release_year is None) or (song.release_year==0):

            # run query search
            query = 'artist:"{0}" AND recording:"{1}" AND status:"official"'.format(song.artist, song.track)
            try:
                results = mb.search_recordings(query=query, strict=True)
                # extract fields of interest (earliest release year & associated album)
                release_year, album = extract_mb_results(results)

            except:
                release_year = 1
                album = ''

            if type(album) is unicode:
                album = unidecode.unidecode(album)

            if (release_year==1) or (album==''):
                album = song.album # revert to playlist album result
                print 'NO RESULTS',

            # show result, catching errors
            try:
                print '{0:d} {1} by {2}'.format(release_year, song.track, song.artist),
            except:
                release_year = 1
                print 'INVALID {0:d} {1} by {2}'.format(release_year, song.track, song.artist),

            try:
                print 'on {0}'.format(album)
            except:
                album = song.album
                print 'INVALID on {0}'.format(album)

            years.append(release_year)
            albums.append(album)

        else:
            release_year = int(song.release_year)
            years.append(release_year)
            albums.append(song.album)

    # update dataframe
    data.loc[:,'release_year'] = years
    data.loc[:,'album'] = albums

    # return data frame
    return data

def extract_mb_results(results):
    '''
    given results from a musicbrainzngs.search_recordings query, extract the
    earliest official release and return the year and album name. If no
    results, return year as 1 and album name as ''
    '''

    # extract all release years
    alldata = []
    if 'recording-list' in results:
        recording_list = results['recording-list']
        for rec in recording_list:

            if 'release-list' in rec:
                release_list = rec['release-list']

                for rel in release_list:

                    # get album name
                    if 'title' in rel:
                        title = rel['title']
                    else:
                        title = []

                    # get album release year
                    if 'date' in rel:
                        match = re.search(r'\d\d\d\d', rel['date'])
                        if match:
                            year = int(match.group())
                        else:
                            year = []
                    else:
                        year = []

                    # store data
                    alldata.append((year, title))

    # identify earliest matching release
    if alldata:
        data = min(alldata, key=lambda x: x[0])
    else:
        data = (1, '')

    return data

def count_list(stringlist, break_words=True):
    '''
    Given a list of strings, return a list of tuples containing each unique
    word or phrase and its number of occurances (ignoring case).
    If break_words is true, then strings are broken into individual words which
    are then compared.
    If break_words if false, strings are not broken into words, and the input
    strings/phrases are compared.
    '''

    # combine all strings and split into a list of words, if requested
    if break_words:
        word_list = re.findall(r"[\w']+", ' '.join(stringlist).lower())
    else:
        word_list = stringlist
    word_dict = {}

    # loop over each word/pharase and either add to the dictionary or increment
    for word in word_list:
        if word not in word_dict:
            word_dict[word] = 1
        else:
            word_dict[word] += 1

    # list of tuples with each word and its number of occurances, sorted by
    # decreasing number of occurances
    word_counts = zip(word_dict.keys(), word_dict.values())
    word_counts = sorted(word_counts, key=lambda x:(x[1], x[0]), reverse=True)

    return word_counts

def print_top(word_counts, title='', num=10, quiet=False):
    '''
    given a list of string-count tuples, print the first num entries
    if quiet=True, don't actually print to the screen
    Either way, return a string to print
    '''
    stringlist = []
    ix = 1;

    stringlist.append('==============================')
    stringlist.append('Top %d %s:'%(num, title))
    stringlist.append('------------------------------')
    stringlist.append('#Rank: Occurances Item')
    stringlist.append('==============================')

    if len(word_counts) > num:
        for (word, count) in word_counts[:num]:
            stringlist.append('#%04s: %10s %s'%(str(ix), str(count), str(word)))
            ix += 1
    else:
        for (word, count) in word_counts:
            stringlist.append('#%04s: %10s %s'%(str(ix), str(count), str(word)))
            ix += 1

    fullstring = '\n'.join(stringlist) +'\n'
    if not quiet:
        print fullstring

    return fullstring

def save_counts(word_counts, filename='counts.txt'):
    '''
    Save list of string-count tuples in a text file with the name filename
    '''
    filename = os.path.abspath(filename)
    f = open(filename, 'w')
    data = ['%03d\t%s'%(b,a) for (a,b) in word_counts]
    f.write('\n'.join(data))
    f.close()

    return None

def save_song_info(infolist=[], headerstr=None, pattern='{2:d}\t{0}\t{1}\n',
                   filename='output.txt'):
    '''
    Save the list of info in infolist to the file given by filename using
    pattern to format each item in the list into a string. If headerstr is
    provided, this is inserted at the start of the file
    '''

    f = open(filename, 'w')
    if headerstr:
        f.write(headerstr+'\n')

    for item in infolist:
        f.write(pattern.format(*item))

    f.close()
    return

def backtoback(df, column='artist'):
    '''
    Back to back artists in the playlist? Check and return a datafram with the
    repeated results
    '''

    # sort data frame by time stamp
    df = df.sort_values('time')

    # initialize output
    back2back = pd.DataFrame(columns=df.columns)
    curr_artist = ''
    curr_tracks = []
    runs = []

    # compare each entry to previous
    first = True
    to_add = None
    for (ix, song) in df.iterrows():
        if first:
            prev_song = song
            first = False
            continue

        # compare current to prev & store if its a repeat
        if prev_song[column] == song[column]:
            curr_artist = prev_song[column]
            curr_tracks.append(prev_song['track'])

            back2back = back2back.append(prev_song)
            to_add = song


        else:
            if to_add is not None:
                curr_tracks.append(to_add['track'])
                back2back = back2back.append(to_add)
                runs.append((curr_artist, curr_tracks))
                curr_artist = ''
                curr_tracks = []
                to_add = None

        # reset previous
        prev_song = song

    # return
    return (runs, back2back)



def main():
    '''
    Use contained functions to import, analyze, and save results of xpn
    playlist data
    '''

    # parse inputs (lookback vs AtoZ)
    args = sys.argv[1:]
    if not args:
        atoz = False
        lookback = False
    else:
        lookback = args[0]=='lookback'
        atoz = args[0]=='atoz'

    if lookback:
        print 'Running lookback analysis'
    elif atoz:
        print 'Running AtoZ analysis'

    if lookback:
        # gather song data
        filename = 'lookback_playlistdata.csv'
        data = read_playlist_data(filename=filename,
                                  rawbasename='./scrape_playlist/lookback/playlistdata_*.csv',
                                  updateMB=False, savedata=True)

    elif atoz: # standard A to Z analysis

        #gather song data and update MB info
        filename = 'playlistdata.csv'
        data = read_playlist_data(filename=filename, updateMB=True, savedata=True,
                                  resetMB=False)
        artists = list(data['artist'])
        years = list(data['release_year'])
        tracks = list(data['track'])
        first_word = [x.split()[0] for x in tracks]
        albums = list(data['album'])
        times = pd.to_datetime(data['time']).tolist()

        # count unique artists, titles, and title words
        unique_track_words = count_list(tracks, break_words=True)
        unique_tracks = count_list(tracks, break_words=False)
        unique_artists = count_list(artists, break_words=False)
        unique_years = count_list(years, break_words=False)
        unique_first_word = count_list(first_word, break_words=False)

        # save title, title-word and artists counts
        save_counts(unique_track_words, filename='top_title_words.txt')
        save_counts(unique_tracks, filename='top_titles.txt')
        save_counts(unique_artists, filename='top_artists.txt')
        save_counts(unique_years, filename='top_years.txt')
        save_counts(unique_first_word, filename='top_first_word.txt')

        # get top 20 lists
        top_track_words = print_top(unique_track_words, title='title words', num=50, quiet=True)
        top_tracks = print_top(unique_tracks, title='titles', num=50, quiet=True)
        top_artists = print_top(unique_artists, title='artists', num=50, quiet=True)
        top_years = print_top(unique_years, title='release year', num=50, quiet=True)

        # save top 20 lists to a text file
        f = open('last_updated.txt')
        nowstr = f.readline()
        f.close()
        f = open('top_50_lists.txt', 'w')
        f.write('WXPN A to Z analysis #XPNAtoZ www.xpn.org, by Lena Bartell\n')
        f.write(nowstr)
        f.write('\n\n')
        f.write(top_artists)
        f.write('\n')
        f.write(top_tracks)
        f.write('\n')
        f.write(top_track_words)
        f.write('\n')
        f.write(top_years)
        f.close()

        # Number of tracks in each letter
        first_letter = [x[0].lower() for x in tracks]

        letter_counts = dict((x,0) for x in list(string.ascii_lowercase))
        unique_letters = count_list(first_letter, break_words=False)
        letter_counts.update(unique_letters)
        unique_letters = zip(letter_counts.keys(), letter_counts.values())
        save_counts(unique_letters, filename='letter_counts.txt')

        # gather some summary data/notes

        # - totals
        letters_played = len([x[0] for x in unique_letters if x[1]>0])
        pct_played = letters_played / 26. * 100.
        letters_played_str = '{0:d}'.format(letters_played, pct_played)

#        elapsed = max(times)-min(times)
        elapsed = dt.datetime(2017, 4, 7, 23, 0) - dt.datetime(2017, 4, 7, 19, 0) + \
            dt.datetime(2017, 4, 8, 17, 0) - dt.datetime(2017, 4, 8, 10, 0) + \
            max(times) - dt.datetime(2017, 4, 9, 12, 0)

        # - last updated
        filename = os.path.abspath('last_updated.txt')
        f = open(filename, 'r')
        last_update_str = f.readline().strip()
        f.close()

        # text file summary
        f = open(os.path.abspath('summary.txt'),'w')
        f.write('{0}:\t{1}\n'.format('Elapsed time', str(elapsed)))
        f.write('{0}:\t{1}\n'.format('Songs', len(tracks)))
        f.write('{0}:\t{1}\n'.format('Artists', len(unique_artists)))
        f.write('{0}:\t{1}\n'.format('Song Titles', len(unique_tracks)))
        f.write('{0}:\t{1}\n'.format('Song Title Words', len(unique_track_words)))
        f.write('{0}:\t{1}\n'.format('Letters / Characters', letters_played_str))
        f.write('{0}:\t{1}\n'.format(*last_update_str.split(': ')))
        f.close()

        # get back-to-back artists
        b2b, _ = backtoback(data)

        f = open(os.path.abspath('back2back.txt'), 'w')
        ix = 1;
        for tup in b2b:
            f.write('{0:d}\t{1}\t{2}\n'.format(ix, tup[0], '\t'.join(tup[1])))
            ix += 1
        f.close()


if __name__ == '__main__':
  main()

