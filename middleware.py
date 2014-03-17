import sys
from zt.ver4 import zutil
from django.db import transaction
from app.models import ErrorLog


def _process_response(request, response):
	from django.utils.text  import compress_string
	from django.utils.cache import patch_vary_headers
	import re
	from datetime import datetime, timedelta

	if not response.has_header('Expires') or not response.has_header('Cache-Control'):
#		response['Expires'] = (datetime.now() + timedelta(days=3)).strftime('%A %d %b %Y 00:00:00 GMT')
		response['Cache-Control'] = 'public, must-revalidate'

	# It's not worth compressing non-OK or really short responses.
	if response.status_code != 200 or len(response.content) < 200:
		return response
			
	patch_vary_headers(response, ('Accept-Encoding',))
			
	# Avoid gzipping if we've already got a content-encoding.
	if response.has_header('Content-Encoding'):
		return response
			
	# MSIE have issues with gzipped respones of various content types.
	if "msie" in request.META.get('HTTP_USER_AGENT', '').lower():
		ctype = response.get('Content-Type', '').lower()
		if not ctype.startswith("text/") or "javascript" in ctype:
			return response
			
	ae = request.META.get('HTTP_ACCEPT_ENCODING', '')
	if 'gzip' not in ae:
		return response
			
	response.content = compress_string(response.content)
	response['Content-Encoding'] = 'gzip'
	response['Content-Length'] = str(len(response.content))
	return response


# ==============================================================================
class ErrorLoggingMiddleware(object):
	def process_exception(self, request, exception):
		from helpers import logTraceback
		# Saves user, url, traceback to db
		traceback = zutil.get_stack_string()
		url = request.get_full_path()
		
		try:
			user = request.user
			logTraceback(traceback, url, user)
		except:
			logTraceback(traceback, url)


# ==============================================================================
class HistoryMiddleware(object):
	def save_history(self, request):
		try:
			self._save_history(request)
		except:
			import sys
			print
			print
			print 'ERROR historymiddleware:', sys.exc_info()[0], sys.exc_info()[1]
			print zutil.get_stack_string()
		
	def _save_history(self, request):

		default_page_info = ['', {}, get_expiration()]
		curr_page = request.get_full_path()
		if curr_page[-1] != '/':	# don't save url of images, files, etc...
			return None

		c_host = request.META.get('HTTP_HOST', '')
		last_page = request.META.get('HTTP_REFERER', '').split(c_host)[-1].strip()
		# This may be a cross-host request.  HTTP_REFERRER may not contain the HTTP_HOST.
		# In that case, the last_page would be meaningless anyway.
		if last_page.lower().startswith('http'):
			last_page = ''
			
		# Using this so that we don't have to update the session if it doesn't
		# change which will lighten the load on the DB
		is_changed = False
		
		# cura_history is a dictionary of lists
		# The first item in the list is the url
		# the second item is the data data for that page
		if 'cura_history' not in request.session.keys():
			is_changed = True
		cura_history = request.session.setdefault('cura_history', {})

		if curr_page != last_page and last_page != '':
			# Don't want to get into a loop
			# Have to do this for the plus signs
			if cura_history.get(last_page, default_page_info)[0] != curr_page:
				cura_history.setdefault(curr_page, [])
				cura_history[curr_page] = [last_page, {}]
				is_changed = True

		# Saves the last page's post data into cura_history session
		if request.method == 'POST':
			post = request.POST
			if last_page not in cura_history.keys():
				cura_history.setdefault(last_page, default_page_info)
				is_changed = True
				
			last_history = cura_history[last_page] or []
			
			# Make sure the last history info has 3 items
			while len(last_history) < 3:
				last_history.append('')
				is_changed = True
				
			last_history[1] = post
			last_history[2] = get_expiration()
			cura_history[last_page] = last_history
				
		if is_changed:
			cura_history.setdefault(curr_page, [])
			
			# Make sure that the page info has 3 items
			while len(cura_history[curr_page]) < 3:
				cura_history[curr_page].append('')
				
			# Make sure the second item is a dictionary
			cura_history[curr_page][1] = cura_history[curr_page][1] or {}
				
			# Update the expiration date to a day from now
			cura_history[curr_page][2] = get_expiration()
			
			# Only update if it's been changed to lighten DB usage
			request.session['cura_history'] = cura_history

		return None

	def process_request(self, request):
		try:
			self.save_history(request)
		except:
			from helpers import logTraceback
			# Saves user, url, traceback to db
			traceback = 'There was an error saving the past!\n\n%s' % zutil.get_stack_string()
			url = request.get_full_path()
			try:
				user = request.user
				logTraceback(traceback, url, user)
			except:
				logTraceback(traceback, url)
		return None


# ==============================================================================
class CacheAndGzipMiddleware(object):
	
	def process_response(self, request, response):
		return _process_response(request, response)


# ==============================================================================
# ============================ HELPER FUNCTIONS ================================
# ==============================================================================
def get_expiration(dt=None):
	from datetime import datetime, timedelta
	if not dt:
		dt = datetime.now()
	dt += timedelta(days=1)
	return dt.date()
	
# ==============================================================================
@transaction.commit_on_success
def clean_all_session_history():
	"""
	Anytime a page is saved to the history middleware, it is given an expiration
	date of a day. This function will check all pages' expiration dates and remove
	them from the session if they are expired.
	"""
	from django.contrib.sessions.models import Session
	from datetime import datetime, timedelta, date
	history_expire_date = get_expiration()
	for sess in Session.objects.all():
		expire_date = sess.expire_date
		sess_key = sess.session_key
		session_data = sess.get_decoded()
		cura_history = session_data.get('cura_history', {})
		
		for page, page_info in cura_history.items():
			
			while len(page_info) < 3:
				page_info.append('')
					
			page_info[1] = page_info[1] or {}
			page_info[2] = page_info[2] or history_expire_date
				
			if isinstance(page_info[2], datetime):
				page_info[2] = page_info[2].date()
			if page_info[2] <= datetime.now().date():
				# Past the expiration date, remove the url from the session
				# history
				del cura_history[page]
					
		# Load the new history data into the session data
		session_data['cura_history'] = cura_history
				
		# Save the session data
		Session.objects.save(sess_key, session_data, expire_date)
	return