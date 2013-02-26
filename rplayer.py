#!/usr/bin/python

import simplejson as json
import subprocess, sys, mplayer, re
import select, termios, tty, time, datetime



DB_FILE = "rplayer.sqlite"



import urllib
class AppURLopener(urllib.FancyURLopener):
    version = "RPlayer/0.1"

urllib._urlopener = AppURLopener()



### <PERSISTENCE> ###
from sqlobject import *

persistence_conn = connectionForURI( "sqlite:%s" % DB_FILE )
persistence_conn.dbEncoding = 'UTF8'
#persistence_conn.debug = True
class Post(SQLObject):
    _connection = persistence_conn
    title = UnicodeCol()
    rUrl = UnicodeCol()
    mUrl = UnicodeCol(default=None)
    rId = UnicodeCol()
    played = DateTimeCol(default=None)
    like = IntCol(default=0)

try: Post.select().count()
except: Post.createTable( connection=persistence_conn )
### </PERSISTENCE> ###

import curses
# es:
#   print ansi_color( curses.COLOR_RED )
#   print ansi_color( bold=True )
def ansi_color(string, color=None, bold=False):
    attr = []
    if color:
        attr.append(str(color+30))
    if bold:
        attr.append('1')
    return '\x1b[%sm%s\x1b[0m' % (';'.join(attr), string)

####################################

import datetime

def plur(n, sing, plur):
    if n == 1:
        return sing
    return plur

def timesince(d, now=None, reversed=False):
    """
Takes two datetime objects and returns the time between d and now
as a nicely formatted string, e.g. "10 minutes". If d occurs after now,
then "0 minutes" is returned.

Units used are years, months, weeks, days, hours, and minutes.
Seconds and microseconds are ignored. Up to two adjacent units will be
displayed. For example, "2 weeks, 3 days" and "1 year, 3 months" are
possible outputs, but "2 weeks, 3 hours" and "1 year, 5 days" are not.

Adapted from http://blog.natbat.co.uk/archive/2003/Jun/14/time_since
"""
    chunks = (
        (60 * 60 * 24 * 365, ('%d year', '%d years')),
        (60 * 60 * 24 * 30, ('%d month', '%d months')),
        (60 * 60 * 24 * 7, ('%d week', '%d weeks')),
        (60 * 60 * 24, ('%d day', '%d days')),
        (60 * 60, ('%d hour', '%d hours')),
        (60, ('%d minute', '%d minutes'))
    )
    # Convert datetime.date to datetime.datetime for comparison.
    if not isinstance(d, datetime.datetime):
        d = datetime.datetime(d.year, d.month, d.day)
    if now and not isinstance(now, datetime.datetime):
        now = datetime.datetime(now.year, now.month, now.day)

    if not now:
        now = datetime.datetime.now()

    delta = (d - now) if reversed else (now - d)
    # ignore microseconds
    since = delta.days * 24 * 60 * 60 + delta.seconds
    if since <= 0:
        # d is in the future compared to now, stop processing.
        return '0 minutes'
    for i, (seconds, name) in enumerate(chunks):
        count = since // seconds
        if count != 0:
            break
    result = plur(count, *name) % count
    if i + 1 < len(chunks):
        # Now get the second item
        seconds2, name2 = chunks[i + 1]
        count2 = (since - (seconds * count)) // seconds2
        if count2 != 0:
            result += ', ' + plur( count2, *name2 ) % count2
    return result

####################################

def likesource():
    while True:
        yesterday = datetime.datetime.now() - datetime.timedelta(hours=24)
        posts = Post.select( Post.q.like > 0 ).filter( Post.q.played < yesterday ).orderBy( "random()" )

        if not posts.count():
            print ">>> rated tracks source exausted"
            return

        for post in posts:
            yesterday = datetime.datetime.now() - datetime.timedelta(hours=24)
            if post.played and post.played < yesterday:
                yield post


def newsource():
    after = None
    while True:
        posts = Post.selectBy( played=None )

        if not posts.count():
            after = retrieve(after)
            continue

        for post in posts:
            yield post

def oldsource():
    while True:
        yesterday = datetime.datetime.now() - datetime.timedelta(hours=24)
        posts = Post.select( AND( Post.q.played != None, Post.q.like == 0 ) ).filter( Post.q.played < yesterday ).orderBy( "random()" )

        if not posts.count():
            print ">>> old unrated tracks source exausted"
            return

        for post in posts:
            yesterday = datetime.datetime.now() - datetime.timedelta(hours=24)
            if post.played and post.played < yesterday:
                yield post

def roundrobinextractor(sources):
    while True:
        for source in sources[:]:
            try:
                while True:
                    post = yield source.next()
                    if not post:
                        break
            except StopIteration:
                sources.remove(source)

import random
def randomextractor(sources):
    while True:
        source = sources[random.randint(0,len(sources)-1)]
        try:
            while True:
                post = yield source.next()
                if not post:
                    break
        except StopIteration:
            sources.remove(source)

def playlist():
    sources = []

    o = oldsource()
    n = newsource()
    l = likesource()

    if options.sources:
        for source in options.sources:

            if source == "o":
                sources.append( o )
            elif source == "n":
                sources.append( n )
            elif source == "l":
                sources.append( l )
    else:
        sources.append( n )

    print ">>> sources:", "".join(options.sources) if options.sources else "n"

    while True:
        posts = []

        if options.roundrobinextractor:
            extractor = roundrobinextractor
        elif options.randomextractor:
            extractor = randomextractor
        else:
            extractor = roundrobinextractor

        print ">>> extractor:", "roundrobin" if options.roundrobinextractor else ("random" if options.randomextractor else "random")

        extractor = extractor(sources)

        for post in extractor:
            if post in posts:
                extractor.send( post )
                continue
            posts.append( post )
            if len(posts) == 10:
                if options.random:
                    random.shuffle(posts)
                yield posts
                posts = []


def retrieve( after ):
    print ">>> retrieving posts after:%s" % (after if after else None)

    url = "http://www.reddit.com/r/%s/.json" % SUBREDDIT
    if after:
        url += ( "?after=%s" % after )

    filename,headers = urllib.urlretrieve(url)

    with open(filename) as f:
        jdata = json.load(f)

    jchildren = jdata["data"]["children"]
    print ">>> %d posts retrieved" % len(jchildren)

    post = None

    for jpost in jchildren:

        jpost = jpost["data"]

        dupe = Post.selectBy( rId=jpost["id"] ).count() > 0

        print ">>> post: dupe:%s id:%s title:%s" % (str(dupe), jpost["id"], jpost["title"])

        if dupe:
            continue
        
        post = Post( title=jpost["title"], rUrl=jpost["url"],  rId=jpost["id"] )

    return jdata["data"]["after"]

def quvi(rUrl):
    try:
        print ">>> retrieving media url..."
        quvi = subprocess.check_output(["quvi", "--verbosity", "quiet", rUrl], stderr=subprocess.STDOUT)
        jvideo = json.loads( quvi )
        return jvideo["link"][0]["url"]
    except:
        return None

def mainloop(index, total, post):
    played = post.played
    post.played = datetime.datetime.now()

    if not post.mUrl:
        post.mUrl = quvi(post.rUrl)

    if not post.mUrl:
        return -2

    print ">>>",
    print ansi_color("(%d/%d)" % (index+1, total), curses.COLOR_GREEN, True),
    print "playing",
    print ansi_color("[%s]" % ( ("%s ago" % timesince(played)) if played else ""), curses.COLOR_YELLOW, True),
    print ansi_color("[%s]" % (u"\u2605" * post.like), curses.COLOR_RED, True),
    print ansi_color( post.title.strip(), bold=True ),
    print "..."

    player.loadfile( post.mUrl )

    paused = False
    end = 0
    tot = 0
    while True:

        percent = 0
        try:
            percent = int(player.percent_pos)
        except:
            pass

        pos = 0
        try:
            pos = float(player.time_pos)
        except:
            pass
        
        if player.percent_pos == player.time_pos == None:
            if end == 3:
                player.stop()
                if tot == 0:
                    print ">>> current media url invalid, retrying..."
                    mUrl = quvi(post.rUrl)
                    if mUrl == post.mUrl:
                        print ">>> got the same media url, skipping..."
                        return index + 1
                    post.mUrl = mUrl
                    end = 0
                    player.loadfile( post.mUrl )
                    continue
                        
                return index + 1
            else:
                end += 1
                continue
        else:
            end = 0

        s = []
        s.append( ansi_color( "%d%% %.0fs" % ( percent, pos ), curses.COLOR_GREEN, True ) )
        s.append( " | " )
        s.append( ansi_color( "[%s]" % ("p" if post.played else "u"), curses.COLOR_YELLOW, True ) )
        s.append( " " )
        s.append( ansi_color( "[%s]" % (u"\u2605" * post.like), curses.COLOR_RED, True ) )
        s.append( " | " )
        s.append( re.sub( r"\([^)]+\)", lambda m: ansi_color(m.group(0), bold=True), "(p)revious (n)ext (r)eplay (q)uit re(s)ume pau(s)e like(12345) unlike(-) (u)nplay" ) )
        sys.stdout.write("\r>>> %s > " % "".join(s))

        if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
            c = sys.stdin.read(1)

            if   c == "r":
                player.stop()
                return index

            if   c == "p":
                player.stop()
                return index - 1

            if   c == "n":
                player.stop()
                return index + 1

            elif c == "q":
                player.stop()
                sys.exit()

            elif c == "s":
                if paused:
                    player.run()
                else:
                    player.pause()

            elif c == "u":
                post.played = None

            elif c == "-":
                player.stop()
                post.like = -1
                return index + 1

            elif 0x30 < ord(c) <= 0x35:
                post.like = ord(c) - 0x30
    
        time.sleep(.1)
        tot += 1


#####################################
from optparse import OptionParser

parser = OptionParser( usage = "usage: %prog" )

parser.add_option("-n", "--new",  action="append_const", const="n", dest="sources", help="build playlist from new tracks source. This is the default.")
parser.add_option("-l", "--like", action="append_const", const="l", dest="sources", help="build playlist from rated tracks source")
parser.add_option("-o", "--old",  action="append_const", const="o", dest="sources", help="build playlist from old unrated tracks source")

#parser.add_option("-S", "--source-order",  action="store", dest="source_order", default="id", choices="id ^id random played ^played like ^like", default="id", help="order by wich tracks are generated by the source")

parser.add_option("-r", "--random", action="store_true", dest="random", default=False, help="shuffle playlist before playing")

parser.add_option("-R", "--random-extractor",      action="store_true", dest="randomextractor",      default=False, help="choose the source for each track in the playlist randomly")
parser.add_option("-B", "--roundrobin-extractor",  action="store_true", dest="roundrobinextractor",  default=False, help="choose the source for each track in the playlist roundrobinly. This is the default.")

(options, args) = parser.parse_args()

##################

SUBREDDIT = args[0] if len(args) > 0 else "listentothis/hot"
 
player = mplayer.Player( ("-novideo", "-cache-min", "20") )

old_settings = termios.tcgetattr(sys.stdin)
tty.setcbreak(sys.stdin.fileno())
try:

   for posts in playlist(): 
        print ">>> %d posts found" % len(posts)

        index = 0
        while True:
            if index >= len(posts):
                break
            post = posts[index]
            ret = mainloop( index, len(posts), post )
            if ret == -2:
                del posts[index]
                continue
            index = max(0, ret)
            print




finally:
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    print


