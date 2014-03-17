import helpers
from helpers import render_custom_page, render_data_page, getFieldItem
from app.models import *
from app.forms import FilterIssueNoteHistory
from app.templatetags import dicthandlers, permissions
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction, connection
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import get_object_or_404
from django.db.models import Q

@login_required
@user_passes_test(permissions.isAdmin, login_url='/user/not_authorized/')
def issue_notes(request):
	'''
	The historicalization view for Issue Notes. The page has a filter form that
	uses a GET request to process the Historicalized Issue Notes data.
	'''
	
	form = FilterIssueNoteHistory(request.GET)
	template = 'historical_issue_notes.html'
	base_template = permissions.getBase(request)
	title = 'Issue Changelog'
	
	page = request.GET.get('page', 1)
	if page == 'all':
		page = -1
	else:
		page = int(page)
	num_per_page = 50
				
	if page < 0:
		num_per_page = -1
		page = 1
		
	issues, total_issues = process_request(request, page, num_per_page)
	is_large_query = total_issues > 200
	
	uis, dummy = helpers.__page_numbers_html(request, total_issues, num_per_page, 
			page, 1, extra='', form_id='id_changelog_form', use_custom_form=True)
	
	ui = uis[0]
	
	return render_custom_page(request, template, locals())

# ===============================================================================
def process_request(request, page, num_per_page):
	
	# Process the form query
	wheres = []
	ewheres = []
	wheres_args = []
	ewheres_args = []
			
	post = request.GET
	if post.get('issue_id'):
		ids = post['issue_id'].split()
		id_n_sql = []
		id_e_sql = []
		for word in ids:
			
			# TODO: Be able to use >, <, >=, <=
			op = '='			
			
			if word.isdigit():
				id_n_sql.append('n.issue=%s')
				id_e_sql.append('e.issue=%s')
				wheres_args.append(word)
				ewheres_args.append(word)
		wheres.append(' OR '.join(id_n_sql))
		ewheres.append(' OR '.join(id_e_sql))
		
	if post.get('change_date_start'):
		cds = helpers.getDate(post['change_date_start'])
		wheres.append('n.entry_date >= %s')
		wheres_args.append(cds.strftime('%Y-%m-%d 00:00'))
		ewheres.append('e.add_date >= %s')
		ewheres_args.append(cds.strftime('%Y-%m-%d 00:00'))
		
	if post.get('change_date_end'):
		from datetime import timedelta
		cde = helpers.getDate(post['change_date_end']) + timedelta(days=1)
		wheres.append('n.entry_date < %s')
		wheres_args.append(cde.strftime('%Y-%m-%d 00:00'))
		ewheres.append('e.add_date < %s')
		ewheres_args.append(cde.strftime('%Y-%m-%d 00:00'))
		
	if post.get('issue_type'):
		it = get_object_or_404(IssueType, pk=post['issue_type'])
		wheres.append('n.note ILIKE %s')
		wheres_args.append('%%type%%to %%%s%%' % it.type)
		
	if post.get('issue_disposition'):
		id = get_object_or_404(IssueDisposition, pk=post['issue_disposition'])
		wheres.append('n.note ILIKE %s')
		wheres_args.append('%%disposition%%to %%%s%%' % id.disposition)
		
	if post.get('project'):
		p = get_object_or_404(IssueProject, pk=post['project'])
		wheres.append('n.note ILIKE %s')
		wheres_args.append('%%project%%to %%%s%%' % p.name)
				
	rtr = post.get('related_to_relation')
	if rtr and rtr.isdigit():		
		rt = get_object_or_404(Person, pk=rtr)
		wheres.append('n.note ILIKE %s')
		ewheres.append('(r.reply_type=1 AND ea.person=%s)')
		rt_name = '%s %s' % (rt.first_name, rt.last_name)
		wheres_args.append('%%reported by%%to %%%s%%' % rt_name)
		ewheres_args.append(int(rtr))
		
	total_issues = 0
	issues = []
	if post.get('page'):
		# Only show issues after a search has been done. This will help
		# with the page's initial load time.
		issues, total_issues = get_historicalized_notes_and_emails(wheres, ewheres,
				wheres_args, ewheres_args, page, num_per_page)
				
	return issues, total_issues

# ===============================================================================
def get_historicalized_notes_and_emails(wheres=[], ewheres=[], wheres_params=[],
		ewheres_params=[], page=1, total_per_page=50):
	'''	
	  * wheres is a list of queries that will be matched up against the notes.
	  * ewheres is a list of queries that will be matched up against the emails.
	
	Returns a sorted dictionary of lists. The first value in the inner list is the Issue ID
	and the second value is a list of either Notes or Emails for the Issue.	
	'''
		
	issues = {}
	
	wheres = ' AND '.join(wheres).strip()
	if wheres:
		wheres = 'AND ' + wheres
	
	ewheres = ' AND '.join(ewheres).strip()
	if ewheres:
		ewheres = 'AND ' + ewheres
				
	# Get the paged issue numbers
	sql = '''
	SELECT COALESCE(n.issue, e.issue) FROM notes n
	FULL JOIN emails e ON (e.issue=n.issue)
	LEFT JOIN replies r ON (r.email=e.emailsid AND r.reply_type=1)
	LEFT JOIN email_addresses ea ON (ea.email_addressesid=r.email_address)
	WHERE (n.is_active AND n.issue IS NOT NULL AND n.note ILIKE '%%%%<span%%%%' %s) OR (
		e.is_active AND e.issue IS NOT NULL AND e.add_date < (SELECT entry_date FROM notes WHERE issue=e.issue ORDER BY entry_date LIMIT 1) AND e.was_received %s)
	GROUP BY COALESCE(n.issue, e.issue)
	ORDER BY COALESCE(n.issue, e.issue)
	''' % (wheres, ewheres)
		
	cursor = connection.cursor()	

	cursor.execute(sql, wheres_params+ewheres_params)

	# Adding the -1 in there to make sure that the list isn't empty which
	# can lead to all sorts of problems in the SQL
	issueids = [str(_[0]) for _ in cursor.fetchall()] + ['-1']
		
	# Get the total number of issues found. Take off one for the -1 appended to
	# the list
	total_issues = len(issueids) - 1
		
	# Get the limit and offset values to determine which section of the Issues
	# to use in the query based on which page the user is on.
	if page < 0:
		limit = 0
		offset = total_per_page
	else:
		if page < 1: page = 1
		limit = (page-1)*total_per_page
		offset = page*total_per_page
			
	issueids = issueids[limit:offset]
	issueids = ','.join(issueids).strip()
			
	# Query the database. Order by the issue, note date and note id in that
	# order
	
	sql = """
	(
	SELECT DISTINCT ON (date_trunc('minute', n.entry_date), md5(n.note)) 'note' as type, n.notesid as id, n.entry_date, n.category, n.issue AS "issue_id",
					COALESCE(trim(replace(substring(n.note, '.*[Tt]itle.*?to (?:&quot;|"|'')(.+?)(?:&quot;|"|'').*'), E'\n', ' ')), '') AS "changed_title", i.title AS "current_title",
					COALESCE(trim(replace(substring(n.note, '.*[Ii]ssue [Pp]roject.*?to (?:&quot;|"|'')(.+?)(?:&quot;|"|'').*'), E'\n', ' ')), '') AS "changed_project", ip.name AS "current_project",
					COALESCE(trim(replace(substring(n.note, '.*[Ii]ssue [Tt]ype.*?to (?:&quot;|"|'')(.+?)(?:&quot;|"|'').*'), E'\n', ' ')), '') AS "changed_issue_type", it.type AS "current_issue_type",
					COALESCE(trim(replace(substring(n.note, '(?:.*disposition of ''(.+?)''.*)|(?:.*[Ii]ssue [Dd]isposition.*?to (?:&quot;|"|'')(.+?)(?:&quot;|"|'').*)'), E'\n', ' ')), '') AS "changed_issue_disposition", id.disposition AS "current_issue_disposition",
					COALESCE(trim(replace(substring(n.note, '.*[Rr]eported [Bb]y.*?to (?:&quot;|"|'')(.+?)(?:&quot;|"|'').*'), E'\n', ' ')), CASE WHEN p.peopleid IS NOT NULL THEN (COALESCE(p.last_name, '') || ', ' || COALESCE(p.first_name, '')) ELSE '' END) AS "changed_reported_by", CASE WHEN p2.peopleid IS NOT NULL THEN (COALESCE(p2.last_name, '') || ', ' || COALESCE(p2.first_name, '')) ELSE '' END AS "current_reported_by",
					COALESCE(trim(replace(substring(n.note, '.*[Tt]ickets.*?to (?:&quot;|"|'')(.+?)(?:&quot;|"|'').*'), E'\n', ' ')), '') AS "changed_tickets", COALESCE(i.tickets, '') AS "current_tickets",
					nt.type AS "note_type",
					n.entry_date AS "change_date",
					trim(replace(replace(substring(n.note from '<span.*?>(.+)</span>'), E'\n', ' '), '&quot;', '"')) AS "raw_note"
	FROM notes n
	JOIN issues i ON (n.issue=i.issuesid)
	LEFT JOIN issue_dispositions id ON (id.issue_dispositionsid=i.issue_disposition)
	LEFT JOIN people p ON (p.peopleid=n.issue_person)
	LEFT JOIN people p2 ON (p2.peopleid=i.person)
	LEFT JOIN note_types nt ON (nt.note_typesid=n.type)
	LEFT JOIN note_categories nc ON (nc.note_categoriesid=n.category)
	LEFT JOIN auth_user au ON (au.id=n.created_by)
	LEFT JOIN issue_projects ip ON (ip.issue_projectsid=i.issue_projectsid)
	LEFT JOIN issue_types it ON (it.issue_typesid=i.issue_type)
	WHERE n.is_active AND n.issue IN (%s
		) AND n.note ILIKE '%%%%<span%%%%'
	ORDER BY date_trunc('minute', n.entry_date), md5(n.note), n.category, n.notesid
	)
	UNION ALL
	(
	SELECT DISTINCT ON(data.issue,date_trunc('day',data.add_date)) 'email' as type, data.emailsid as id, data.add_date, null as category, data.issue AS "issue_id",
                COALESCE(trim(replace(substring(data.subject, E'\[zt [0-9]+\] (.+)'), E'', ' ')), ' ') AS "changed_title", 
                data.title AS "current_title",
                'ledsSuite' AS "changed_project", 
                data.name AS "current_project",
                'Incident' AS "changed_issue_type", 
                data.type AS "current_issue_type",
                'In Support' AS "changed_issue_disposition", 
                data.disposition AS "current_issue_disposition",
                CASE WHEN data.people_id_1 IS NOT NULL THEN (COALESCE(data.last_name_1, '') || ', ' || COALESCE(data.first_name_1, ''))  ELSE '' END AS "changed_reported_by", 
                CASE WHEN data.people_id_2 IS NOT NULL THEN (COALESCE(data.last_name_2, '') || ', ' || COALESCE(data.first_name_2, '')) ELSE '' END AS "current_reported_by",
                '' AS "changed_tickets", 
                COALESCE(data.tickets, '') AS "current_tickets",
                'Email' AS "note_type",
                data.add_date AS "change_date",
                data.body AS "raw_note"                              
                
				FROM 
				(SELECT 
				   e.emailsid,e.add_date,e.subject,e.body,e.issue,i.title,i.tickets,
				   p.last_name as last_name_1,p.peopleid as people_id_1,p.first_name as first_name_1,
				   p2.peopleid as people_id_2,p2.last_name as last_name_2,
				   p2.first_name as first_name_2,id.disposition,it.type,ip.name                  
				FROM emails e
				JOIN issues i ON (e.issue=i.issuesid)
				JOIN replies r ON (r.email = e.emailsid)
				LEFT JOIN email_addresses ea ON (ea.email_addressesid=r.email_address)
				LEFT JOIN people p ON (ea.person=p.peopleid)
				LEFT JOIN people p2 ON (p2.peopleid=i.person)
				LEFT JOIN issue_dispositions id ON (id.issue_dispositionsid=i.issue_disposition)
				LEFT JOIN issue_projects ip ON (ip.issue_projectsid=i.issue_projectsid)
				LEFT JOIN issue_types it ON (it.issue_typesid=i.issue_type)
				WHERE e.is_active 
				AND e.issue IN (%s)
				AND e.add_date < (SELECT entry_date FROM notes WHERE issue=e.issue
				ORDER BY entry_date LIMIT 1) 
				  ) AS data 
				  ORDER BY data.issue,date_trunc('day',data.add_date)
				  ) 
				ORDER BY 5, 3, 2, 4

			""" % (
					issueids,
					issueids,
					)	
								
	cursor = connection.cursor()
	cursor.execute(sql, [])

	cols = [column[0] for column in cursor.description]
	rows = cursor.fetchall()
	
	def _check_row(r):
		fields = ['changed_project', 'changed_issue_type', 'changed_issue_disposition',
				'changed_reported_by', 'changed_tickets', 'changed_title']
		for f in fields:
			if r[f] and r[f].strip():
				return True		
		return False
	
	def _fill_history_fields(row, prev_row):
		fields = ['project', 'issue_type', 'issue_disposition',
				'reported_by', 'tickets', 'title']
			
		for f in fields:
			ckey = 'changed_%s' % f
			hkey = 'history_%s' % f
			crow = row.get(ckey, '')
			
			# Change "None" values to blank spaces so they display as removed
			# on the History page.
			if crow is None or crow.lower() == 'none':
				row[ckey] = ' '
				
			# Remove the change value if it hasn't actually changed
			if crow in [prev_row.get(ckey, ''), prev_row.get(hkey, '')]:
				row[ckey] = ''
								
			# Set the history (grayed out) value to current changed value. 
			# If there was no current change then use the previous changed value.
			# If the previous changed value doesn't exist, then use the previous
			# history value.
			row[hkey] = crow or prev_row.get(ckey, None) or prev_row.get(hkey, ' ')
			row[hkey] = row[hkey].strip()
		return row
		
	prev_row = {}
	for row in rows:
		row = dict(zip(cols, row))
		
		if row['issue_id'] != prev_row.get('issue_id'):
			prev_row = {}
		
		row = _fill_history_fields(row, prev_row)
		
		if _check_row(row):
			issues.setdefault(row['issue_id'], {'label': '%s - %s' % (
					row['issue_id'], row['current_title']),
				'changes': []})
			issues[row['issue_id']]['changes'].append(row)
		
		prev_row = row
		
	issues = sorted(issues.items())
		
	return issues, total_issues

# ==============================================================================
def export_issue_history_to_excel(request):
	"""
	Takes the data given by the current Issue Changelog page query and outputs
	it in an Excel format.
	"""
	from xlwt import Workbook, easyxf
	
	book = Workbook()
	sheet = book.add_sheet('Issue Changelog')
	
	# To write to the sheet do this: page.write(row, column, 'TEXT')
	
	# Styles are added like so:
	#	sheet.write(1,1,date(2009,3,18),easyxf(
    #		'''font: name Arial, bold True;
    #		borders: left thick, right thick, top thick, bottom thick;
    #		pattern: pattern solid, fore_colour red;''',
    #		num_format_str='YYYY-MM-DD'
    #	))
	
	
	# Get all issues for the given query
	issues, total_issues = process_request(request, 1, -1)
	
	row_count = 0
	changed_font_style = 'font: name Arial, color black, height 160;'
	history_font_style = 'font: name Arial, color gray40, height 160;'
	header_font_style = 'font: name Arial, color black, bold True, height 160;'
	label_font_style = 'font: name Arial, color black, bold True;'
	label_border_style = 'borders: left thick, right thick, top thick, bottom medium;'
	label_style = easyxf('%s %s alignment: horizontal left;' % (
						label_font_style, label_border_style))
	date_format = 'MM/DD/YYYY'
	headers = ['Date', 'Title', 'Issue Disposition', 'Issue Type', 'Issue Project',
			'Reported By', 'Tickets']
	fields = ['entry_date', 'title', 'issue_disposition', 'issue_type', 'project',
			'reported_by', 'tickets']
	field_widths = [3000, 12000, 7000, 3500, 3000, 3000, 7000]
	
	header_styles = [
			# Middle columns
			easyxf('%s borders: left no_line, bottom no_line, top no_line, right no_line;' % header_font_style),
			# Left column
			easyxf('%s borders: left thick, bottom no_line, top no_line, right no_line;' % header_font_style),
			# Right column
			easyxf('%s borders: left no_line, bottom no_line, top no_line, right thick;' % header_font_style),
	]
	
	# These are all the possible border style combinations that a changelog cell
	# can have
	changes_borders = [
			# Middle columns
			'borders: left no_line, bottom no_line, top no_line, right no_line;',
			# Left column
			'borders: left thick, bottom no_line, top no_line, right no_line;',
			# Right column
			'borders: left no_line, bottom no_line, top no_line, right thick;',
			# Top column
			'borders: left no_line, bottom no_line, top thin, right no_line;',
			# Bottom column
			'borders: left no_line, bottom thick, top no_line, right no_line;',
			# Top-Left 
			'borders: left thick, bottom no_line, top thin, right no_line;',
			# Top-Right
			'borders: left no_line, bottom no_line, top thin, right thick;',
			# Bottom-Left
			'borders: left thick, bottom thick, top no_line, right no_line;',
			# Bottom-Right
			'borders: left no_line, bottom thick, top no_line, right thick;',
	]
	
	# Since we can only have a limited amount of styles created, we have to
	# do it this way and instantiate them all at one time.
	all_styles = {
			'changes': {
					'default': [easyxf('%s %s' % (changed_font_style,
							b)) for b in changes_borders],
					'date': [easyxf('%s %s alignment: horizontal left;' % (
							changed_font_style, b), 
							num_format_str=date_format
					) for b in changes_borders],
			},
			'history': {
					'default': [easyxf('%s %s' % (history_font_style,
							b)) for b in changes_borders],
					'date': [easyxf('%s %s alignment: horizontal left;' % (
							history_font_style, b),
							num_format_str=date_format
					) for b in changes_borders],
			},
	}
	
	# ----------------------------------------------------------------------				
	def _get_text_and_style_for_changes(row, col, change, field, total_rows, 
			total_cols):
		# Gets the correct text output and the appropriate style for the given
		# cell
		changed_row = ''
		ckey = 'changed_%s' % field
		hkey = 'history_%s' % field
		font_style = 'changes'
		cell_format = 'default'
		
		if field in change.keys():
			changed_row = change.get(field, '')	
			
		if not changed_row and ckey in change:
			changed_row = change.get(ckey, '')
		
		if not changed_row:
			changed_row = change.get(hkey, '')
			font_style = 'history'
		
		if field.lower().endswith('date'):
			cell_format = 'date'
			
		borderid = _get_borders(row, col, total_rows, total_cols)
		style = all_styles[font_style][cell_format][borderid]
				
		return changed_row, style
	
	# ----------------------------------------------------------------------
	def _get_header_style(col, total_cols):
		# Returns the style for the header in the given column
		if col == 0:
			return header_styles[1]
		elif col == total_cols-1:
			return header_styles[2]
		return header_styles[0]
		
	# ----------------------------------------------------------------------
	def _get_borders(row, col, total_rows, total_cols):
		# Gets the border according to the changes_border list
		
		# bid is the index of the border in the changes_border list
		bid = 0		
		if col == 0:
			if row == 0:
				# Top-Left
				bid = 5
			elif row == total_rows-1:
				# Bottom-Left
				bid = 7
			else:
				# Left
				bid = 1
		elif col == total_cols-1:
			if row == 0:
				# Top-Right
				bid = 6
			elif row == total_rows-1:
				# Bottom-Right
				bid = 8
			else:
				# Right
				bid = 2
		elif row == 0:
			# Top
			bid = 3
		elif row == total_rows-1:
			# Bottom
			bid = 4
		
		return bid
	
	for issue in issues:
		# Get the issue info dictionary
		issue = issue[1]
		
		# Insert a blank row
		sheet.row(row_count)
		row_count += 1
		
		# Insert the label
		sheet.write_merge(row_count, row_count, 0, 6, issue.get('label', ''),
				label_style
		)
		row_count += 1
				
		# Insert the headers (entry_date, title, issue_disposition, issue_type,
		#		issue_project, reported_by, tickets)
		row = sheet.row(row_count)
		row_count += 1
		for i, header in enumerate(headers):
			row.write(i, header, _get_header_style(i, len(headers)))
		
		for i, width in enumerate(field_widths):
			sheet.col(i).width = width
				
		changes = issue.get('changes', [])
		change_count = len(changes)
		for r, change in enumerate(changes):
			# Insert a row for each change
			row = sheet.row(row_count)
			row_count += 1
			
			# Go through each column and insert the changed text with the
			# correct style
			for c, field in enumerate(fields):
				changed_text, style = _get_text_and_style_for_changes(r, c, 
						change, field, change_count, 7)
						
				# Add in the Issue ID to the text for the title field
				if field == 'title' and changed_text:
					changed_text = '%s - %s' % (change['issue_id'], changed_text)
					
				row.write(c, changed_text, style)
			
	# Return the newly created file
	response = HttpResponse(mimetype='application/vnd.ms-excel')
	response['Content-Disposition'] = 'attachment; filename=issue_changelog.xls'
	book.save(response)
	return response