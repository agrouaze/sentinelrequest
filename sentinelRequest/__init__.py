from __future__ import print_function
import os,sys
import numpy as np
import datetime
import requests
from lxml import etree
import logging

logging.basicConfig()
logger = logging.getLogger("sentinelRequest")
logger.setLevel(logging.INFO)


urlapi='https://scihub.copernicus.eu/apihub/search'

#download_scihub_url={  # %s : uuid
#    "main" : "https://scihub.copernicus.eu/apihub/odata/v1/Products('%s')/$value",
#    "alt"  : "https://scihub.copernicus.eu/apihub/odata/v1/Products('%s')/",
#    "ql"   : "https://scihub.copernicus.eu/apihub/odata/v1/Products('%s')/Products('Quicklook')/$value"
#    }

# remove_dom
xslt='''<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
<xsl:output method="xml" indent="no"/>

<xsl:template match="/|comment()|processing-instruction()">
    <xsl:copy>
      <xsl:apply-templates/>
    </xsl:copy>
</xsl:template>

<xsl:template match="*">
    <xsl:element name="{local-name()}">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
</xsl:template>

<xsl:template match="@*">
    <xsl:attribute name="{local-name()}">
      <xsl:value-of select="."/>
    </xsl:attribute>
</xsl:template>
</xsl:stylesheet>
'''

remove_dom=etree.XSLT(etree.fromstring(xslt))


def download_scihub(filename,user='guest', password='guest'):
    safe=scihubQuery(filename=filename, user=user, password=password)
    urldl=download_scihub_url['ql'] % safe[filename]['uuid']
    # todo : use wget for downloads
    xmlout=requests.get(urldl,auth=(user,password))
    
    return xmlout


def scihubQuery(date=None,dtime=datetime.timedelta(hours=3) ,lonlat=None, ddeg=0.0 ,filename='S1*', datatake=False, duplicate=False, query=None, user='guest', password='guest', show=False):
    """
    query='(platformname:Sentinel-1 AND sensoroperationalmode:WV)' 
    input:
        date: [ start, stop ] 
        if [ date ], dtime will be used to compute start and stop
        lonlat : ( lon, lat ) or [(lon1,lat1),(lon2,lat2),...] or shapely object
        duplicate : if True, will return safes with same prodid
        ddeg : float rounding precision in deg
    """
    
    q=[]
    dateformat="%Y-%m-%dT%H:%M:%S.%fZ"
    dateformat_alt="%Y-%m-%dT%H:%M:%S"
    footprint=""
    datePosition=""
        
    if date:
        try:
            len(date)
        except:
            date=[date]
        if len(date) == 2:
            startdate=date[0].strftime(dateformat)
            stopdate=date[1].strftime(dateformat)
        else:
            startdate=(date[0]-dtime).strftime(dateformat)
            stopdate=(date[0]+dtime).strftime(dateformat)
            
        datePosition="(beginPosition:[%s TO %s] OR endPosition:[%s TO %s])" % (startdate , stopdate , startdate, stopdate)
        q.append(datePosition)
        
    q.append("filename:%s" % filename)
    
    if query:
        q.append("(%s)" % query)
    
    if lonlat:
        if not hasattr(lonlat,'to_wkt'):
            from shapely.geometry import Polygon,Point
            try:
                shape=Polygon(lonlat)
            except (TypeError,ValueError):
                shape=Point(lonlat)
        else:
            shape=lonlat.exterior.convex_hull.simplify(0.1, preserve_topology=False)
        
        if ddeg > 0.0:
            shape=shape.buffer(ddeg,resolution=2)
        
        from shapely.wkt import dumps
        wkt_shape=dumps(shape,rounding_precision=2) # .replace("POINT","")
        
        footprint='(footprint:\"Intersects(%s)\" )' % wkt_shape
        q.append(footprint)
    
    
    str_query = ' AND '.join(q)
    
    logger.debug("query: %s" % str_query)
    
    safes={}
    start=0
    count=1 # arbitrary count > start
    while start < count:
        xmlout=requests.get(urlapi,auth=(user,password),params={"start":start,"rows":100,"q":str_query})
        
        try:
            root = remove_dom(etree.fromstring(xmlout.content))
        except:
            try:
                import html2text
                content=html2text.html2text(str(xmlout.content))
            except:
                logger.info("html2text not found. dumping raw html")
                content=xmlout.content
            logger.critical("Error while parsing xml answer")
            logger.critical("query was: %s" % str_query )
            logger.critical("answer is: \n %s" % content)
            return {}
        
        #<opensearch:totalResults>442</opensearch:totalResults>\n
        count=int(root.find(".//totalResults").text)
        #logger.debug("totalResults : %s" % root.find(".//totalResults").text )
        logger.debug("%s" % root.find(".//subtitle").text )
        #logger.debug("got %d entry starting at %d" % (len(root.findall(".//entry")),start))
        
        if len(root.findall(".//entry")) > 0:
            for entry in root.findall(".//entry"):
                #filename=entry.find("str[@name = 'filename']").text
                safe={}
                
                # get all str objects
                for str_entry in entry.findall("str"):
                    safe[str_entry.attrib['name']]=str_entry.text
                # get all int objects
                for int_entry in entry.findall("int"):
                    safe[int_entry.attrib['name']]=int(int_entry.text)
                # get all date objects
                for date_entry in entry.findall("date"):
                    try:
                        safe[date_entry.attrib['name']]=datetime.datetime.strptime(date_entry.text,dateformat)
                    except ValueError:
                        safe[date_entry.attrib['name']]=datetime.datetime.strptime(date_entry.text[0:19],dateformat_alt)
                    
                for link in entry.findall("link"):
                    url_name='url'
                    if 'rel' in link.attrib:
                        url_name="%s_%s" % (url_name, link.attrib['rel'])
                    safe[url_name]=link.attrib['href']
                #safes["%s" % filename] = safe
                
                # append to safes
                if not safes:
                    safes=safe.copy()
                    for field in safes:
                        safes[field]=[safes[field]]
                else:
                    for field in safes:
                        if field not in safe:
                            val=None
                        else:
                            val=safe[field]
                        safes[field].append(val)
                
                start+=1
        #else:
        #    start=-1
        
    if datatake:
        logger.debug("Asking for same datatakes")
        for safe in list(safes['filename']):
            takeid=safe.split('_')[-2]
            safe_rad="_".join(safe.split('_')[0:4])
            safes_datatake=scihubQuery(filename='%s_*_*_*_%s_*' % (safe_rad, takeid),user=user,password=password)
            idup=safes_datatake['filename'].index(safe)
            for field in safes_datatake:
                del safes_datatake[field][idup]
                safes[field]+=safes_datatake[field]
                
            #for safe_datatake,value in safes_datatake.items():
            #    safes[safe_datatake]=value
    if safes and not duplicate:
        # remove duplicate safes
        filenames=safes['filename']
        filenames_radic=[f[0:62] for f in filenames]
        toremove_ind=[] # index list to delete
        for filename_radic in filenames_radic:
            if filenames_radic.count(filename_radic) > 1:
                dup_ind=[i for i,val in enumerate(filenames_radic) if val==filename_radic]
                for i in dup_ind:
                    logger.debug("duplicate prodid : %s" % (safes['filename'][i]))
                
                # keep fist ingested SAFE
                keep_ind=safes['ingestiondate'].index(min([ safes['ingestiondate'][i] for i in dup_ind ]))
                del_ind=list(dup_ind)
                del_ind.remove(keep_ind)
                toremove_ind=toremove_ind+del_ind
        for f in safes.keys():
            for d in reversed(list(set(toremove_ind))):
                del safes[f][d]
                
    if not safes:
        logger.debug("No results from scihub. Will return empty dict")
        
    if show:
        import pandas as pd
        import geopandas as gpd
        import matplotlib.pyplot as plt
        from shapely import wkt
        map = gpd.read_file(gpd.datasets.get_path('naturalearth_lowres')).plot(color='white', edgecolor='black')
        
        if lonlat is not None:
            gdf_sel=gpd.GeoDataFrame({'geometry':[shape]})
            map = gdf_sel.plot(ax=map,color='red',alpha=0.3)
        
        if safes:
            df=pd.DataFrame(safes)
            df['footprint'] = df['footprint'].apply(wkt.loads)
            gdf=gpd.GeoDataFrame(df,geometry='footprint')
            gdf.plot(ax=map,color='blue')
        plt.show()
        

    return safes


       

