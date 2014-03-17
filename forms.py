from django import forms
from django.db import transaction
from lib import form_fields
from lib.form_fields import ZDateTimeField, ZDateField, RelationField
from lib.cura_forms import DynamicAddingForm
from app.templatetags import permissions
from helpers import getFieldItem, get_old_vals_and_new_model, logTraceback
from app.models import *
from django.contrib.auth.models import User
from django.contrib.localflavor.us.us_states import STATE_CHOICES
from django.db.models import Q
from django.shortcuts import get_object_or_404
import helpers

# Need to do this for the modelchoicefields
User.__str__ = helpers.get_user_display
User.__unicode__ = helpers.get_user_display

# ==============================================================================
# ================================ GLOBALS =====================================
# ==============================================================================

class common_widget(forms.TextInput):
	def __init__(self, attrs={'size': '40'}, *args, **kwargs):
		super(common_widget, self).__init__(attrs=attrs, *args, **kwargs)

def commonCharField(attrs={'size':'40'}, *args, **kwargs):
	kwargs.setdefault('widget', common_widget(attrs=attrs))
	return forms.CharField(*args, **kwargs)

def commonCharFieldSmall(attrs={'size': '10'}, *args, **kwargs):
	return commonCharField(attrs=attrs, *args, **kwargs)

def commonTextarea(attrs={'rows': 5, 'cols': 43}, *args, **kwargs):
	kwargs.setdefault('widget', forms.Textarea(attrs=attrs))
	return forms.CharField(*args, **kwargs)

def commonBooleanField(custom_choices=[], *args, **kwargs):
	# Make sure that the custom_choices list has at least two values
	custom_choices += ['Yes', 'No']
	return forms.ChoiceField(
		choices=[(True, custom_choices[0]),(False, custom_choices[1]),], 
		required=False, *args, **kwargs)

# ==============================================================================
class ModelFormManager(forms.ModelForm):

	def is_valid(self, *args, **kwargs):
		valid = super(ModelFormManager, self).is_valid(*args, **kwargs)
		if not valid:
			# Log errors
			output_str = '<ul>'
			for field, errs in self.errors.items():
				for err in errs:
					if not isinstance(err, basestring):
						# For some reason, some errors aren't strings but calling
						# strip() on them turns them into the correct string
						err = err.strip()
					output_str += '<li>%s: %s</li>' % (field, err)
			output_str += '</ul>'
			id = ''
			if self.instance and self.instance.pk:
				id = ' for %s #%s' % (self.Meta.model.__name__, self.instance.pk)
			output_str = 'The %s form%s had some errors:\n\n%s' % (
					self.Meta.model.__name__, id, output_str)
			logTraceback(output_str, is_warning=True)
		return valid

# ==============================================================================
def getOppNotes(model, c):
	from app.contact_views import getOpportunities

	# model is the Note obj
	# c should be a NoteCategory obj
	
	opp_relations = []
	opps = []

	if not c:
		return []

	c = c.category.lower()

	if c == 'person':
		# get the opportunities for the person
		tmp_id = getFieldItem(model, ['person', 'id'], None)
		if tmp_id:
			opp_relations = getOpportunities([tmp_id], [])
	elif c =='organization':
		# get the opportunities for the organization
		tmp_id = getFieldItem(model, ['organization', 'id'], None)
		if tmp_id:
			opp_relations = getOpportunities([], [tmp_id])

	new_cat = getFieldItem(NoteCategory.objects.filter(
			category__iexact='opportunity'), [], None, 0)

	if new_cat and opp_relations and len(opp_relations) > 0:
		for o in opp_relations:
			# Add the notes
			opp = o.opportunity
			attachments = []

			new_model = Note(note=model.note, type=model.type,
					category=new_cat, opportunity=opp, 
					created_by=model.created_by,
					)

			opps.append(new_model)

	return opps

# ==============================================================================
def getOppAndCampaignNotes(note):
	# note is the original Note object

	cat = note.category
	campaign = note.campaign

	# notes will be a list of lists.
	# The first item of the inner list will be the new Note object.
	# The second will as list of NoteAttachment objects
	notes = getOppNotes(note, cat)

	if campaign:
		# save a new campaign note
		c_att = []
		new_cat = NoteCategory.objects.get_or_create(category='Campaign')[0]
		c = Note(note=note.note, type=note.type,
				category=new_cat, campaign=campaign, 
				created_by=note.created_by)

		notes.append(c)

	return notes


# ==============================================================================
def getYearChoices(year_ext=30):
	from datetime import datetime, timedelta
	
	now = datetime.now()
	choices = []
	curr_year = now.year + year_ext
	min_year = now.year - year_ext
	while curr_year >= min_year:
		choices.append([curr_year, curr_year])
		curr_year -= 1
	return choices

# ==============================================================================
class SystemNoteForm(ModelFormManager):

	def __init__(self, *args, **kwargs):
		# validate - a check to validate fields or not. Most useful for editing
		#			 a model and adding a system note even when the model may be
		#			 invalid.
		validate = True
		if 'validate' in kwargs:
			validate = kwargs['validate']
			del kwargs['validate']
		super(SystemNoteForm, self).__init__(*args, **kwargs)
		self._note_category = None  # This needs to be changed in the form
		self._set_old_vals()
		self._validate = validate
		
	def is_valid(self, *args, **kwargs):
		if not self._validate:
			return True
		return super(SystemNoteForm, self).is_valid(*args, **kwargs)

	def _set_note_category(self, nc):
		self._note_category = get_object_or_404(NoteCategory,
				category__iexact=nc)

	def _set_old_vals(self):
		self.old_vals = {}
		if self.instance:
			self.old_vals = self.instance._get_vals_dict()
		return

	def _get_differences_note(self, custom_new={}, custom_removed={},
			append_fields_after_changelog=[]):
		import datetime
		# custom_new is a dictionary of strings representing newly added items. 
		# 	The key is the label used in the note for the items.
		# custom_removed is like custom_new. It is a dictionary of strings 
		# 	representing removed items. The key is the label used in the note 
		# 	for the items.
		# append_fields_after_changelog is a list of fields that should be
		# 	appended to the Note after the system note section.
		sorted_fields = self.old_vals.keys()
		sorted_fields.sort()
		i = self.instance
		ov = self.old_vals
		changes = []
		is_changed = False

		for f in sorted_fields:
			old_field = ov.get(f, None) or None
			new_field = getattr(i, f) or None			

			if old_field == new_field:
				continue
			elif str(old_field) == new_field:
				continue
			elif isinstance(old_field, datetime.datetime) and isinstance(
					new_field, datetime.date) and old_field.date() == new_field:
				continue
			elif isinstance(old_field, datetime.date) and isinstance(new_field, 
					datetime.datetime) and old_field == new_field.date():
				continue

			if not new_field:
				new_field = ''

			if f not in append_fields_after_changelog:
				update_text = 'Changed %s' % f
				if ov.get(f, None):
					update_text += ' from "%s"' % old_field
				update_text += ' to "%s"' % new_field
				changes.append(update_text)
				is_changed = True
			elif new_field:
				is_changed = True

		# Process the custom_new items
		for lbl, news in custom_new.items():
			for n in news:
#				if isinstance(n, list):
#					n = ', '.join(n).strip()
				changes.append('Added %s "%s"' % (lbl, n))
				is_changed = True

		# Process the custom_removed items
		for lbl, rems in custom_removed.items():
			for r in rems:
#				if isinstance(r, list):
#					r = ', '.join(r).strip()
				changes.append('Removed %s "%s"' % (lbl, r))
				is_changed = True

		note = None
		if is_changed:
			user = self.data.get('user', None)
			if user:
				user = get_object_or_404(User, pk=user)
			else:
				# Use the SupportUser
				from app.email_helpers import getSupportUser
				user = getSupportUser()

			# Append the fields in append_fields_after_changelog list
			extra_note_body = []
			for f in append_fields_after_changelog:
				ofield = ov.get(f, '') or ''
				nfield = getattr(i, f) or ''
				if nfield and str(nfield).strip() and str(nfield).strip() != str(ofield).strip():
					extra_note_body.append(str(nfield).strip())
			extra_note_body = '\n\n'.join(extra_note_body)
			if extra_note_body.strip() and changes:
				extra_note_body = '\n\n%s' % extra_note_body.strip()

			note_body = '<span style="color:#888; font-style:italic;">%s</span>%s' % ('\n'.join(changes), extra_note_body)
			ntype = get_object_or_404(NoteType, type__iexact='note')
			note = Note(category=self._note_category, type=ntype,
					created_by=user, note=note_body)
		return note


# ==============================================================================
# ==============================================================================
class LogForm(forms.Form):
	
	pages = (("100", "100"), ("500", "500"), ("1000", "1000"), 
			("5000", "5000"), ("10000", "10000"))

	# == Customers ==
	customers_choices = []
	customers = Customer.objects.all()
	for customer in customers:
		customers_choices.append([customer.id, customer.name])
	customers_choices.append(['all', 'All'])

	customers = forms.ChoiceField(required=False, choices=customers_choices)
	# ===============

	# == Versions ==
	versions_choices = []
	for item in Version.objects.all():
		versions_choices.append([item.pk, str(item.version)])
	versions_choices.append(['all', 'All'])
	
	versions = forms.ChoiceField(required=False, choices=versions_choices)
	# ==============

	# == Levels ==
	levels_choices = []
	for item in Level.objects.all():
		levels_choices.append([item.id, item.level])
	levels_choices.append(['all', 'All'])
		
	levels = forms.ChoiceField(required=False, choices=levels_choices)
	# ============

	pages = forms.ChoiceField(choices=pages)
	start_logdate = ZDateTimeField()
	end_logdate = ZDateTimeField()
	computerid = forms.CharField(max_length=10000, required=False)
	usersid = forms.CharField(max_length=10000, required=False)
	message = forms.CharField(max_length=10000, required=False)
	logid = forms.CharField(max_length=10000, required=False)

# ==============================================================================
# ============================ Contacts ========================================
# ==============================================================================
def clean_form_dependents(form):
	# Used to allow the Person/Organization Add Forms to recognize
	# errors in the PhoneNumber, Address and EmailAddress Add forms
	# form is a DynamicAddingForm object

	cd = form.cleaned_data
	d = form.data
	m = form.instance or form._meta.model()
	import re
	for f in form.form_dependents:
		tmpf = f(m, data=d)
		fname = re.sub('([A-Z])', r' \1', tmpf.form_name).strip()
		if not tmpf.is_valid():
			err = tmpf.errors.get('__all__', [])[:1]
			form.errors.setdefault(fname, forms.util.ErrorList())
			form.errors[fname] += err
			raise forms.ValidationError(err[0])
	return cd

# ==============================================================================
class ContactFilterForm(forms.Form):

	searchbox = forms.CharField()
	import_from_excel_file = forms.FileField()
	
	st_choices = [('', 'All')] + list(STATE_CHOICES)
	state = forms.ChoiceField(choices=st_choices)
	
	def __init__(self, *args, **kwargs):
		super(ContactFilterForm, self).__init__(*args, **kwargs)
		
		choices = []
		choices += [('Contact Types', [(_type.pk, _type.type) for _type in OrganizationType.objects.all()])]
		choices += [('Other', [
			('only_newsletter', 'Only E-Newsletter Contacts'),
			('only_orgs', 'Only Organizations'),
			('only_people', 'Only People'),
			('only_people_without_orgs', 'Only People w/out Orgs'),
			('only_unhandled', 'Only Unhandled'),
		])]
		self.fields['filters'] = form_fields.MultiComboboxSelectMultipleValues(
			choices=choices, required=False, widget=forms.SelectMultiple)

# ==============================================================================
class AddPhoneNumberForm(DynamicAddingForm):
	
	def __init__(self, fkey_model=None, *args, **kwargs):
		
		choices = PhoneType.objects.all().order_by('type').values_list('id', 
				'type')

		fkey_name = fkey_model.__class__.__name__.lower()

		field_opts = {
				'number': {
					'field': commonCharField,
					'kwargs': {'label':''},
				},
				'type': {
					'field': forms.ChoiceField,
					'kwargs': {'choices': choices, 'label':''},
				},
		}

		super(AddPhoneNumberForm, self).__init__("AddPhoneNumberForm",
				fkey_name, fkey_model, field_opts, 
				[[['type', 'id'], ['number']]], SavePhone, False, False, 
				*args, **kwargs)

	class Meta:
		model = PhoneNumber

# ==============================================================================
class AddAddressForm(DynamicAddingForm):

	def __init__(self, fkey_model=None, *args, **kwargs):

		st_choices = [('', '')] + list(STATE_CHOICES)
		fkey_name = fkey_model.__class__.__name__.lower()

		field_opts = {
				'address': {
					'field': commonCharField,
					'kwargs': {'label': 'Street'},
				},
				'city': {
					'field': commonCharField,
					'kwargs': {'label': 'City',},
				},
				'state_province': {
					'field': forms.ChoiceField,
					'kwargs': {'label': 'State', 'choices': st_choices,},
				},
				'zip': {
					'field': commonCharField,
					'kwargs': {'label': 'Zip',},
				},
		}

		layout = [[['address']], 
				[['city']], 
				[['state_province']], 
				[['zip']]]

		super(AddAddressForm, self).__init__("AddAddressForm",
				fkey_name, fkey_model, field_opts, 
				layout, SaveAddress, True, False,
				*args, **kwargs)

	class Meta:
		model = Address		

# ==============================================================================
class AddEmailAddressForm(DynamicAddingForm):

	def __init__(self, fkey_model=None, *args, **kwargs):

		fkey_name = fkey_model.__class__.__name__.lower()
		field_opts = {
				'email': {
					'field': commonCharField,
					'kwargs': {'label': '', 'required': True},
				},
				'is_primary': {
					'field': forms.BooleanField,
					'kwargs': {'label': '', 'required': False},
				},
		}

		layout = [[['email'], ['is_primary']]]

		super(AddEmailAddressForm, self).__init__("AddEmailAddressForm",
				fkey_name, fkey_model, field_opts, 
				layout, SaveEmailAddress, False, False,
				*args, **kwargs)
	
	def is_valid(self):
		valid = super(AddEmailAddressForm, self).is_valid()
		cd = self.dirty_data or self.data
		dd = {}
		for k, v in dict(cd).items():
			if isinstance(v, list):
				dd[k] = v[0]
			else:
				dd[k] = v

		if not dd:
			# If there are no email addresses, then skip the rest of validation
			return True
		
		is_primarys = []
		is_emails = False
		for k, v in dd.items():
			if k.startswith('%s_is_primary'%self.form_name):
				is_primarys.append(k)
			is_emails = is_emails or k.startswith('%s_email'%self.form_name)
			
		if not is_emails:
			# If there are no email addresses, the form is valid
			return True
		
		if not is_primarys and is_emails:
			# There needs to be at least one primary email
			self.errors.setdefault('__all__', forms.util.ErrorList())
			self.errors['__all__'] += forms.util.ErrorList([
					'There needs to be at least one primary email address.'])
			valid = False
		elif len(is_primarys) != 1:
			# There can only be one primary email
			self.errors.setdefault('__all__', forms.util.ErrorList())
			self.errors['__all__'] += forms.util.ErrorList([
					'There can only be one primary email address.'])
			valid = False
				
		return valid


	class Meta:
		model = EmailAddress

# ==============================================================================
class AddMobileAddressForm(DynamicAddingForm):

	def __init__(self, fkey_model=None, *args, **kwargs):

		st_choices = [('', '')] + list(STATE_CHOICES)
		fkey_name = fkey_model.__class__.__name__.lower()

		field_opts = {
				'address': {
					'field': forms.CharField,
					'kwargs': {'label': 'Street'},
				},
				'city': {
					'field': forms.CharField,
					'kwargs': {'label': 'City',},
				},
				'state_province': {
					'field': forms.ChoiceField,
					'kwargs': {'label': 'State', 'choices': st_choices,},
				},
				'zip': {
					'field': forms.CharField,
					'kwargs': {'label': 'Zip',},
				},
		}

		layout = [[['address']], 
				[['city']], 
				[['state_province']], 
				[['zip']]]

		super(AddMobileAddressForm, self).__init__("AddMobileAddressForm",
				fkey_name, fkey_model, field_opts, 
				layout, SaveAddress, True, False,
				*args, **kwargs)

	class Meta:
		model = Address

# ==============================================================================
class AddPersonForm(ModelFormManager):

	last_name = commonCharField()
	first_name = commonCharField()
	additional_info = forms.CharField(widget=forms.Textarea, required=False)
	role = commonCharField(required=False)
	title = commonCharField(required=False)
	receives_newsletter = commonBooleanField()

	form_dependents = [AddEmailAddressForm, AddAddressForm, AddPhoneNumberForm]

	def __init__(self, *args, **kwargs):
		super(AddPersonForm, self).__init__(*args, **kwargs)
		self.fields['organization'] = forms.ModelChoiceField(
				queryset=Organization.unarchived_objects.all().order_by('name'),
				empty_label='None', 
				required=False)
	
	def is_valid(self, *args, **kwargs):
		valid = super(AddPersonForm, self).is_valid(*args, **kwargs)
		try:
			valid = clean_form_dependents(self) and valid
		except:
			import sys
			print 'ERROR VALIDATING addpersonform:', sys.exc_info()[0], sys.exc_info()[1]
			valid = False
		return valid

	class Meta:
		model = Person

# ==============================================================================
class AddPersonMobileForm(ModelFormManager):

	last_name = forms.CharField()
	first_name = forms.CharField()
	additional_info = forms.CharField(widget=forms.Textarea(
		attrs={'rows':2, 'cols':25}
		), required=False)
	title = forms.CharField()
	role = forms.CharField()
	receives_newsletter = commonBooleanField()

	form_dependents = [AddEmailAddressForm, AddMobileAddressForm, AddPhoneNumberForm]

	def __init__(self, *args, **kwargs):
		super(AddPersonMobileForm, self).__init__(*args, **kwargs)
		self.fields['organization'] = forms.ModelChoiceField(
				queryset=Organization.unarchived_objects.all().order_by('name'),
				empty_label='None', 
				required=False)
	
	def is_valid(self, *args, **kwargs):
		valid = super(AddPersonMobileForm, self).is_valid(*args, **kwargs)
		try:
			valid = clean_form_dependents(self) and valid
		except:
			import sys
			print 'ERROR VALIDATING addpersonmobileform:', sys.exc_info()[0], sys.exc_info()[1]
			valid = False
		return valid

	class Meta:
		model = Person

# ==============================================================================
class AddOrganizationForm(ModelFormManager):

	name = commonCharField()
	alias = commonCharFieldSmall()
	additional_info = forms.CharField(widget=forms.Textarea, required=False)

	form_dependents = [AddEmailAddressForm, AddAddressForm, AddPhoneNumberForm]

	def __init__(self, *args, **kwargs):
		super(AddOrganizationForm, self).__init__(*args, **kwargs)
		self.fields['install'] = forms.ModelChoiceField(
				queryset=Install.objects.all(),
				empty_label='None',
				required=False)
		self.fields['agency_logins'] = forms.ModelChoiceField(
				queryset=User.objects.filter(is_staff=False, is_active=True,
					is_superuser=False).order_by('first_name', 'last_name', 'username'),
				empty_label='None',
				required=False)
	
	def is_valid(self, *args, **kwargs):
		valid = super(AddOrganizationForm, self).is_valid(*args, **kwargs)
		try:
			valid = clean_form_dependents(self) and valid
		except:
			import sys
			print 'ERROR VALIDATING addorganizationform:', sys.exc_info()[0], sys.exc_info()[1]
			valid = False
		return valid

	class Meta:
		model = Organization

# ==============================================================================
class AddOrganizationMobileForm(ModelFormManager):

	name = forms.CharField()
	additional_info = forms.CharField(widget=forms.Textarea(
		attrs={'rows':'2', 'cols':'25'}), required=False)

	form_dependents = [AddEmailAddressForm, AddMobileAddressForm, AddPhoneNumberForm]

	def __init__(self, *args, **kwargs):
		super(AddOrganizationMobileForm, self).__init__(*args, **kwargs)
		self.fields['install'] = forms.ModelChoiceField(
				queryset=Install.objects.all(),
				empty_label='None',
				required=False)
		self.fields['agency_logins'] = forms.ModelChoiceField(
				queryset=User.objects.filter(is_staff=False, is_active=True,
					is_superuser=False).order_by('first_name', 'last_name', 'username'),
				empty_label='None',
				required=False)
	
	def is_valid(self, *args, **kwargs):
		valid = super(AddOrganizationMobileForm, self).is_valid(*args, **kwargs)
		try:
			valid = clean_form_dependents(self) and valid
		except:
			import sys
			print 'ERROR VALIDATING addorganizationmobileform:', sys.exc_info()[0], sys.exc_info()[1]
			valid = False
		return valid

	class Meta:
		model = Organization

# ==============================================================================
# =============================== NOTES ========================================
# ==============================================================================

def get_note_types(user):
	import helpers
	
	us = helpers.get_user_setting(user)[0]
	types = NoteType.objects.all().order_by('type')

	if not us:
		return types

	qs = Q()

	if us.is_support:
		qs = qs | Q(is_support=True)

	if us.is_sales:
		qs = qs | Q(is_sales=True)

	if len(qs) == 0:
		return types.none()
	
	return types.filter(qs)


# ==============================================================================
class NoteAttachmentForm(ModelFormManager):
	original_name = commonCharField()

	class Meta:
		model = NoteAttachment

# ==============================================================================
class NoteForm(ModelFormManager):
	
	note = forms.CharField(widget=forms.Textarea(attrs={
		'cols': '45', 'rows': '5'}), required=False)

	def __init__(self, user, *args, **kwargs):
		super(NoteForm, self).__init__(*args, **kwargs)

		types = get_note_types(user)
		organization_sel = Organization.unarchived_objects.all().order_by('name')
		person_sel = Person.objects.exclude(
				organization__type__type__icontains='archive').order_by(
							'last_name', 'first_name')
		install_sel = Install.objects.all()
		issue_sel = Issue.objects.all().order_by('id')
		issue_person = Person.objects.exclude(
				organization__type__type__icontains='archive').order_by(
						'last_name', 'first_name')
		created_by = User.objects.filter(is_staff=True, is_superuser=True, 
				is_active=True).order_by('first_name', 'last_name', 'username')
		category = NoteCategory.objects.all().order_by('category')
		projects = Project.objects.all().order_by('name')
		project_tasks = ProjectTask.objects.all().order_by('name')
#		opps = Opportunity.objects.all().order_by('title')
#		campaigns = Campaign.objects.filter(is_active=True).order_by('name')

#		self.fields['opportunity_sel'] = forms.ModelChoiceField(
#				queryset=opps,
#				empty_label='', 
#				required=False)
#		self.fields['campaign'] = forms.ModelChoiceField(
#				queryset=campaigns,
#				empty_label='',
#				required=False)
		self.fields['organization_sel'] = forms.ModelChoiceField(
				queryset=organization_sel,
				empty_label='', 
				required=False)
		self.fields['person_sel'] = forms.ModelChoiceField(
				queryset=person_sel,
				empty_label='', 
				required=False)
		self.fields['install_sel'] = forms.ModelChoiceField(
				queryset=install_sel,
				empty_label='', 
				required=False)
		self.fields['issue_sel'] = forms.ModelChoiceField(
				queryset=issue_sel,
				empty_label='', 
				required=False)
		self.fields['project_sel'] = forms.ModelChoiceField(
				queryset=projects,
				empty_label='', 
				required=False)
		self.fields['project_task_sel'] = forms.ModelChoiceField(
				queryset=project_tasks,
				empty_label='', 
				required=False)
		self.fields['issue_person'] = forms.ModelChoiceField(
				queryset=issue_person,
				empty_label='', 
				required=False)
		self.fields['created_by'] = forms.ModelChoiceField(
				queryset=created_by,
				empty_label=None)
		self.fields['category'] = forms.ModelChoiceField(
				queryset=category,
				empty_label='')
		self.fields['type'] = forms.ModelChoiceField(
				queryset=types,
				empty_label='')
	
	class Meta:
		model = Note

# ==============================================================================
class ProjectTaskNoteForm(NoteForm):
	def __init__(self, *args, **kwargs):
		super(ProjectTaskNoteForm, self).__init__(*args, **kwargs)
		self.fields['task_status'] = forms.ModelChoiceField(
				queryset=TaskStatus.objects.all().order_by('name'),
				required=True,
				empty_label='')

	@transaction.commit_on_success
	def save(self, *args, **kwargs):
		m = super(ProjectTaskNoteForm, self).save(commit=False, *args, **kwargs)

		# Save the ProjectTask's status
		d = self.data
		stat = get_object_or_404(TaskStatus, pk=d.get('task_status', None))
		old_ts = m.project_task.task_status
		update_message = ''
		if not (old_ts and stat and old_ts.pk == stat.pk):
			# If they aren't the same value, then provide an update message
			update_message = 'Changed task_status'
			if old_ts:
				update_message += ' from "%s"' % m.project_task.task_status.name
			update_message += ' to "%s"' % stat.name
		m.project_task.task_status = stat
		m.project_task.save()
		update_message = update_message.strip()
		if update_message:
			update_message = '<span style="color:#888; font-style:italic;">%s</span>\n\n' % update_message
		m.note = '%s%s' % (update_message, m.note)
		m.save()
		return m

# ==============================================================================
class PersonNoteForm(ModelFormManager):
	
	note = forms.CharField(widget=forms.Textarea(attrs={
		'cols': '45', 'rows': '5'}), required=False)
	title = commonCharField(required=False)
	duplicate_issue = forms.CharField(required=False)

	def __init__(self, user, is_person, is_resolved, *args, **kwargs):
		super(PersonNoteForm, self).__init__(*args, **kwargs)
		
		organization = Organization.unarchived_objects.all().order_by('name')
		person = Person.objects.exclude(
				organization__type__type__icontains='archive').order_by(
						'last_name', 'first_name')
		install = Install.objects.all()
		issue = Issue.objects.all().order_by('id')
		issue_person = Person.objects.exclude(
				organization__type__type__icontains='archive').order_by(
						'last_name', 'first_name')
		created_by = User.objects.filter(is_staff=True, is_superuser=True, 
				is_active=True).order_by('first_name', 'last_name', 'username')
		assigned_to = User.objects.filter(is_staff=True, is_superuser=True, 
				is_active=True).order_by('first_name', 'last_name', 'username')
		category = NoteCategory.objects.all().order_by('category')
		note_type = get_note_types(user)
#		opps = Opportunity.objects.all().order_by('pk')
#		campaigns = Campaign.objects.filter(is_active=True).order_by('name')

#		self.fields['opportunity'] = forms.ModelChoiceField(
#				queryset=opps,
#				empty_label=None, 
#				required=False)
#		self.fields['campaign'] = forms.ModelChoiceField(
#				queryset=campaigns,
#				empty_label='',
#				required=False)
		self.fields['organization'] = forms.ModelChoiceField(
				queryset=organization,
				empty_label=None, 
				required=False)
		self.fields['person'] = forms.ModelChoiceField(
				queryset=person,
				empty_label=None, 
				required=False)
		self.fields['install'] = forms.ModelChoiceField(
				queryset=install,
				empty_label=None, 
				required=False)
		self.fields['issue'] = forms.ModelChoiceField(
				queryset=issue,
				empty_label=None, 
				required=False)
		self.fields['issue_person'] = forms.ModelChoiceField(
				queryset=issue_person,
				empty_label='', 
				required=False)
		self.fields['created_by'] = forms.ModelChoiceField(
				queryset=created_by,
				empty_label=None,
				required=False)
		self.fields['assigned_to'] = forms.ModelChoiceField(
				queryset=assigned_to,
				empty_label='',
				required=False)
		self.fields['category'] = forms.ModelChoiceField(
				queryset=category,
				empty_label=None,
				required=False)
		self.fields['type'] = forms.ModelChoiceField(
				queryset=note_type,
				empty_label='',
				required=True)
		self.fields['issue_project'] = forms.ModelChoiceField(
				queryset=IssueProject.objects.all().order_by('name'),
				empty_label='', required=False)

		# Person page requires the special empty_label
		empty_labels = ['Note', '', True] if is_person else [None, None]
		self.fields['type_dis_relation'] = getIssueTypeDispositionRelationField(
				empty_labels=empty_labels,
				field_names=['issue_type', 'issue_disposition'],
				table_headers=['Issue Type', 'Disposition'],
				required=[False, False],
				is_resolved=is_resolved,
				)

	def clean(self):
		# this is used to make sure that:
		#	* note, note category and note type are filled for notes
		#	* title and issue type/disposition are filled for issues

		clean_data = self.cleaned_data

		note = clean_data.get('note', '')
		note_type = clean_data.get('type', '')
		note_category = clean_data.get('category', '')
		
		title = clean_data.get('title', '')
		type_dis_relation = clean_data.get('type_dis_relation', '')

		if not (note_type and note_category):
			if not (title and type_dis_relation):
				raise forms.ValidationError('''Required fields for a note are: Note type and Note category.
Required fields for an issue are: Title, Issue type and Issue disposition.''')

		return clean_data
	
	class Meta:
		model = Note


# ==============================================================================
class NotesFilterForm(forms.Form):

	filter_issues = commonCharField(required=False)
	filter_start_date = ZDateTimeField(required=False)
	filter_end_date = ZDateTimeField(required=False)
	filter_mobile_contains = forms.CharField(
			widget=forms.TextInput(attrs={'size':'35'}), 
			required=False)
	filter_contains = filter_mobile_contains

	def __init__(self, *args, **kwargs):
		super(NotesFilterForm, self).__init__(*args, **kwargs)
		
		# Default values
		defaults = {
				#'filter_campaigns': Campaign.objects.all().order_by('name'),
				'filter_installs': Install.objects.all().order_by('title'),
				'filter_created_by': User.objects.filter(is_active=True, 
					is_staff=True, is_superuser=True).order_by('first_name', 'last_name', 'username'),
				'filter_types': NoteType.objects.all().order_by('type'),
				'filter_categories': NoteCategory.objects.all().order_by('category'),
				#'filter_opportunities': Opportunity.objects.all(),
				'filter_projects': Project.objects.all().order_by('name'),
				}

#		self.fields['filter_campaigns'] = forms.ModelChoiceField(
#				queryset=defaults['filter_campaigns'],
#				empty_label='All', 
#				required=False)
		self.fields['filter_related_to'] = getRelatedToField(
				field_names=['filter_organizations', 'filter_people'],
				show_all_orgs=True,
				)
#		self.fields['filter_opportunities'] = forms.ModelChoiceField(
#				queryset=defaults['filter_opportunities'],
#				empty_label='All', 
#				required=False)
		self.fields['filter_installs'] = forms.ModelChoiceField(
				queryset=defaults['filter_installs'],
				empty_label='All', 
				required=False)
		self.fields['filter_created_by'] = forms.ModelChoiceField(
				queryset=defaults['filter_created_by'],
				empty_label='All', 
				required=False)
		self.fields['filter_types'] = forms.ModelChoiceField(
				queryset=defaults['filter_types'],
				empty_label='All', 
				required=False)
		self.fields['filter_categories'] = forms.ModelChoiceField(
				queryset=defaults['filter_categories'],
				empty_label='All',
				required=False)
		self.fields['filter_projects'] = forms.ModelChoiceField(
				queryset=defaults['filter_projects'],
				empty_label='All',
				required=False)

# ==============================================================================
# ========================= HISTORICALIZED NOTES ===============================
# ==============================================================================
class FilterIssueNoteHistory(forms.Form):
	change_date_start = ZDateField()
	change_date_end = ZDateField()
	issue_id = commonCharFieldSmall()
	
	def __init__(self, *args, **kwargs):
		super(FilterIssueNoteHistory, self).__init__(*args, **kwargs)
		project_choices = [('', 'All')] + [(p.pk, p.name) for p in IssueProject.objects.all().order_by('name')]
		
		self.fields['project'] = forms.ChoiceField(choices=project_choices)		
		self.fields['type_dis_relation'] = getIssueTypeDispositionRelationField(
				empty_labels=['All', 'All'],
				field_names = ['issue_type', 'issue_disposition'],
				required = [False, False],
				is_resolved=None,
				)
		self.fields['related_to'] = getRelatedToField()
	
	

# ==============================================================================
# ============================= PROJECTS =======================================
# ==============================================================================
class ProjectFilterForm(forms.Form):
	def __init__(self, *args, **kwargs):
		super(ProjectFilterForm, self).__init__(*args, **kwargs)

		project_choices = [('', 'All')]
		max_name_length = 50
		for p in Project.objects.exclude(project_status__name__iexact='completed').order_by('name'):
			name = p.name
			if len(p.name) > (max_name_length + 3):
				name = name[:max_name_length] + '...'
			project_choices.append((p.pk, name))

		admin_choices = [('', 'All')] + [(u.pk, helpers.get_user_display(u)) for u in User.objects.filter(is_active=True, is_superuser=True).order_by('first_name', 'last_name', 'username')]
		self.fields['project'] = forms.ChoiceField(choices=project_choices)
		self.fields['user'] = forms.ChoiceField(choices=admin_choices)
					
		statuses = tuple([(_.name.lower(), _.name) for _ in TaskStatus.objects.all().order_by('name')])

		self.fields['status'] = forms.ChoiceField(choices=[
			('', 'All Statuses'),
			('Specialized', (('-completed', 'Incomplete'),)),
			('Statuses', statuses),
			])


# ==============================================================================
class AddProjectForm(SystemNoteForm):
	
	name = commonCharField()
	version = commonCharFieldSmall(required=False)
	planned_start_date = ZDateTimeField(default_time='00:00', required=False)
	planned_end_date = ZDateTimeField(default_time='00:00', required=False)
	files_link = commonCharField(required=False)
	description = commonTextarea(required=False)
	software_date = ZDateField(required=False)

	contract_date = ZDateField(required=False)
	contract_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])

	kickoff_date = ZDateField(required=False)
	kickoff_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])

	bpr_date = ZDateField(required=False)
	bpr_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])

	cmd_date = ZDateField(required=False)
	cmd_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])

	configuration_date = ZDateField(required=False)
	configuration_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])

	conversion_date = ZDateField(required=False)
	conversion_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])

	training_date = ZDateField(required=False)
	training_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])

	end_user_training_date = ZDateField(required=False)
	end_user_training_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])

	go_live_date = ZDateField(required=False)
	go_live_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])

	acceptance_date = ZDateField(required=False)
	acceptance_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])

	infrastructure_date = ZDateField(required=False)
	infrastructure_is_payment = commonBooleanField(custom_choices=['Payment', 'No Payment'])
	
	def __init__(self, *args, **kwargs):
		super(AddProjectForm, self).__init__(*args, **kwargs)
		color_options = self._meta.model().color_options
		
		usrs = User.objects.filter(is_active=True, is_superuser=True).order_by('first_name', 'last_name', 'username')

		self.fields['contract_color'] = forms.ChoiceField(choices=color_options, required=False)
		self.fields['kickoff_color'] = forms.ChoiceField(choices=color_options, required=False)
		self.fields['bpr_color'] = forms.ChoiceField(choices=color_options, required=False)
		self.fields['cmd_color'] = forms.ChoiceField(choices=color_options, required=False)
		self.fields['configuration_color'] = forms.ChoiceField(choices=color_options, required=False)
		self.fields['conversion_color'] = forms.ChoiceField(choices=color_options, required=False)
		self.fields['training_color'] = forms.ChoiceField(choices=color_options, required=False)
		self.fields['end_user_training_color'] = forms.ChoiceField(choices=color_options, required=False)
		self.fields['go_live_color'] = forms.ChoiceField(choices=color_options, required=False)
		self.fields['acceptance_color'] = forms.ChoiceField(choices=color_options, required=False)
		self.fields['infrastructure_color'] = forms.ChoiceField(choices=color_options, required=False)
		
		self.fields['project_status'] = forms.ModelChoiceField(
				queryset=ProjectStatus.objects.filter(
					is_closed=False).order_by('name'),
				empty_label = '')
		self.fields['project_type'] = forms.ModelChoiceField(
				queryset=ProjectType.objects.all().order_by('name'),
				empty_label = '')
		self.fields['manager'] = forms.ModelChoiceField(
				queryset=User.objects.filter(
					is_superuser=True).order_by('first_name', 'last_name', 'username'),
				empty_label='')
		self.fields['agency_manager'] = forms.ModelChoiceField(
				queryset=Person.objects.all().order_by('last_name', 'first_name'),
				empty_label='', required=False)
		self.fields['organizations'] = form_fields.MultiComboboxSelectMultiple(
				queryset=Organization.unarchived_objects.all().order_by('name'), 
				required=False, widget=forms.SelectMultiple)
		self.fields['contract_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self.fields['kickoff_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self.fields['bpr_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self.fields['cmd_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self.fields['configuration_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self.fields['conversion_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self.fields['training_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self.fields['end_user_training_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self.fields['go_live_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self.fields['acceptance_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self.fields['infrastructure_users'] = form_fields.MultiComboboxSelectMultiple(
				queryset=usrs, required=False, widget=forms.SelectMultiple)
		self._set_note_category('project')

	def clean(self):
		cd = self.cleaned_data
		sd = cd.get('planned_start_date', None)
		ed = cd.get('planned_end_date', None)
		if sd and ed and sd > ed:
			raise forms.ValidationError('The start date cannot be after the end date')
		return cd

	@transaction.commit_on_success
	def save(self, *args, **kwargs):
		m = super(AddProjectForm, self).save(commit=False, *args, **kwargs)
		m.save()
		
		new_dict = {}
		old_dict = {}
		data_dict = dict(self.data)
		
		# Remove the old ProjectOrganization
		old_org_ids = {}
		for po in ProjectOrganization.objects.filter(project=m.pk):
			old_org_ids[po.organization.pk] = po.organization.name
			po.deactivate()

		# Add the new ProjectOrganization
		new_orgs = []
		for oid in data_dict.get('organizations', []):
			oid = int(oid)
			o = get_object_or_404(Organization, pk=oid)
			if oid in old_org_ids:
				del old_org_ids[oid]
			else:
				new_orgs.append(o.name)
			ProjectOrganization(project=m, organization=o).save()
			
		new_dict['organization'] = new_orgs
		old_orgs = old_org_ids.values()
		old_dict['organization'] = old_orgs
		
		fields = [''] + ['%s_users'%stype for stype in self._meta.model.section_type_names if stype]
		
		def _save_project_overview_assigned_user(project, field, old_user_ids):
			stype = fields.index(field)
			new_users = []
			for uid in data_dict.get(field, []):
				uid = int(uid)
				user = get_object_or_404(User, pk=uid)
				if uid in old_user_ids:
					del old_user_ids[uid]
				else:
					new_users.append(helpers.get_user_display(user))
				ProjectOverviewAssignedUser(project=project, user=user, 
						section_type=stype).save()
						
			if new_users: new_users = [', '.join(new_users)]
			new_dict[field] = new_users
			
			old_users = old_user_ids.values()
			if old_users: old_users = [', '.join(old_users)]
			old_dict[field] = old_users
		
		for i, f in enumerate(fields):
			if not f: continue
			
			# Remove the old ProjectOverviewAssignedUsers
			old_user_ids = {}
			for paou in ProjectOverviewAssignedUser.objects.filter(project=m.pk,
					section_type=i):
				old_user_ids[paou.user.pk] = helpers.get_user_display(paou.user)
				paou.deactivate()
			
			# Add the new ProjectOverviewAssignedUsers
			_save_project_overview_assigned_user(m, f, old_user_ids)

		note = self._get_differences_note(new_dict, old_dict, ['description'])
		if note:
			note.project = m
			note.save()

	class Meta:
		model = Project

# ==============================================================================
class AddProjectTaskForm(SystemNoteForm):

	name = commonCharField()
	fb_tickets = commonCharField(required=False)
	description = commonTextarea(required=False)
	start_date = ZDateTimeField(default_time='00:00', required=False)
	end_date = ZDateTimeField(default_time='00:00', required=False)
	duration = commonCharField(required=False)

	def __init__(self, project=None, *args, **kwargs):
		self._validate = True
		if 'validate' in kwargs:
			self._validate = kwargs['validate']
		super(AddProjectTaskForm, self).__init__(*args, **kwargs)
		self.project = project
		id = None
		if self.instance: id = self.instance.pk
		# Get the predecessors
		pts = ProjectTask.objects.filter(project=project.pk)
		if id: pts = pts.exclude(pk=id)
		pts = pts.order_by('pk', 'name')
		
		# Get the assigned users
		aus = [('Third Party', list(ProjectTaskAssignedUser().other_assigned_choices))]
		aus += [('Users', [(u.pk, helpers.get_user_display(u)) for u in User.objects.filter(
				is_superuser=True).order_by('first_name', 'last_name', 'username')])]
		
		self.fields['task_status'] = forms.ModelChoiceField(
				queryset=TaskStatus.objects.all().order_by('name'),
				required=False)
		self.fields['parent'] = forms.ModelChoiceField(
				empty_label='', required=False, queryset=pts)
		self.fields['upper_sibling'] = forms.ModelChoiceField(
				empty_label='', required=False, queryset=pts)
		self.fields['predecessors'] = form_fields.MultiComboboxSelectMultiple(
				queryset=pts, required=False, widget=forms.SelectMultiple)
		self.fields['assigned'] = form_fields.MultiComboboxSelectMultipleValues(
				choices=aus, required=False, widget=forms.SelectMultiple)

		self._set_note_category('project task')

	def clean(self):
		cd = self.cleaned_data

		# Make sure that at least two of the start_date, end_date, duration
		# fields are filled in
		sd = cd.get('start_date', None)
		ed = cd.get('end_date', None)
		dur = cd.get('duration', '')

		# There are a few acceptable options (anything else is an error):
		    # Both dates are given
			# A date and the duration are given
		is_safe_date = (sd and ed) or (sd and dur) or (ed and dur) or not (sd or ed or dur)
		if not is_safe_date:
			date_field_error = forms.util.ErrorList(['Need either both dates or one date and the duration filled.'])

			self.errors.setdefault('start_date', forms.util.ErrorList())
			self.errors.setdefault('end_date', forms.util.ErrorList())
			self.errors.setdefault('duration', forms.util.ErrorList())

			if not sd:  self.errors['start_date'] += date_field_error
			if not ed:  self.errors['end_date'] += date_field_error
			if not dur: self.errors['duration'] += date_field_error
		elif sd and ed and sd >= ed:
			self.errors.setdefault('end_date', forms.util.ErrorList())
			self.errors['end_date'] += forms.util.ErrorList(['End date needs to be after the start date.'])
			
		# If you don't need to validate the form, then just remove all errors
		if not self._validate:
			for k in self.errors.keys():
				del self.errors[k]

		return self.cleaned_data
	
	@transaction.commit_on_success
	def save(self, *args, **kwargs):
		from app.project_views import _save_fkeys_and_get_changes
		
		m = super(AddProjectTaskForm, self).save(commit=False, *args, **kwargs)
		
		# If the task doesn't have a parent nor an upper sibling then
		# it will just be floating in space. Make its upper sibling the
		# last task in the project so we can at least acknowledge its existence.
		if m.project and not m.parent and not m.upper_sibling:
			m.upper_sibling = m.project.get_last_task()
		m.save()
		
		assigned_data = self.data.getlist('assigned')
		
		# other_assigned data
		oas_data = []
		aus_data = []
		for d in assigned_data:
			if isinstance(d, int) or d.isdigit():
				aus_data.append(d)
			else:
				oas_data.append(d)
				
		# Get other assigned values
		new_oas, old_oas = _save_fkeys_and_get_changes(m, oas_data,
				ProjectTaskAssignedUser, [], 'other_assigned', None, None)
				
		# Get assigned users
		new_aus, old_aus = _save_fkeys_and_get_changes(m, aus_data,
				ProjectTaskAssignedUser, ['user'], 'username', User, helpers.get_user_display)
				
		new_aus = new_oas + new_aus
		old_aus = old_oas + old_aus
		
		# Get the predecessors
		data = self.data.getlist('predecessors')
		new_preds, old_preds = _save_fkeys_and_get_changes(m, data,
				ProjectTaskPrecedence, ['predecessor'], 'name', ProjectTask)

		# Get the system change note
		note = self._get_differences_note(
				{'predecessor':new_preds, 'assigned to':new_aus}, 
				{'predecessor':old_preds, 'assigned to':old_aus}, 
				['description'])
		if note:
			note.project_task = m
			note.save()

	class Meta:
		model = ProjectTask

# ==============================================================================
class ProjectInternalCommentAddForm(ModelFormManager):
	class Meta:
		model = ProjectInternalComment

# ==============================================================================
# ============================= ISSUES =========================================
# ==============================================================================
class ResolveIssueForm(ModelFormManager):
	
	information = forms.CharField(widget=forms.Textarea(), required=False)
	title = commonCharField()
	duplicate_issue = forms.CharField(required=False)
	last_edited_at = forms.DateTimeField(required=False)

	def __init__(self, *args, **kwargs):
		# defaults is a dict of querysets or lists of tuples
		# ModelChoiceFields require a queryset and ChoiceFields require
		# a list of tuples
		super(ResolveIssueForm, self).__init__(*args, **kwargs)

		self.fields['type_dis_relation'] = getIssueTypeDispositionRelationField(
				empty_labels=[None, ''],
				field_names=['issue_type', 'issue_disposition'],
				table_headers=['Issue Type', 'Disposition'],
				is_resolved=True,
				required=[True, True],
				)
		self.fields['issue_project'] = forms.ModelChoiceField(
				queryset=IssueProject.objects.all().order_by('name'),
				empty_label='', required=False)
		self.fields['assigned_to'] = forms.ModelChoiceField(
				queryset=User.objects.filter(is_active=True, 
						is_superuser=True).order_by('first_name', 'last_name'),
				empty_label='', required=False)

	def clean_duplicate_issue(self):
		import helpers
		from app import models
		disp = self.cleaned_data['issue_disposition']
		dupe = self.cleaned_data['duplicate_issue']
		if not dupe:
			if 'duplicate' == helpers.getFieldItem(disp, 
					['disposition'], '').lower():
				raise forms.ValidationError('Enter a valid Issue ID')
		elif Issue.objects.filter(pk=dupe).count() == 0:
			raise forms.ValidationError('No Issue matches the given ID')
		return dupe

	class Meta:
		model = Issue
		

# ==============================================================================
class EditIssueFromReportForm(forms.Form):
	def __init__(self, *args, **kwargs):
		super(EditIssueFromReportForm, self).__init__(*args, **kwargs)
		
		self.fields['assigned_to'] = forms.ModelChoiceField(
				User.objects.filter(is_active=True, is_staff=True, 
					is_superuser=True).order_by('first_name', 'last_name', 'username'),
				empty_label='', required=False)
				
		self.fields['issue_disposition'] = forms.ModelChoiceField(
				IssueDisposition.objects.filter(is_for_resolved=False).order_by(
						'disposition'),
				empty_label='', required=False)
				
	def clean(self):
		cd = self.data
		
		if not cd.get('issue_disposition') and not cd.get('assigned_to'):
			raise forms.ValidationError('Field is required')
		
		return cd

# ==============================================================================
class AddIssueForm(ModelFormManager):
	resolved_date = forms.DateTimeField(required=False)
	title = commonCharField()
	tickets = forms.CharField(widget=forms.TextInput(attrs={'size': '52'}), 
			required=False)
	information = forms.CharField(widget=forms.Textarea(), required=False)
	deadline = ZDateTimeField(required=False)
	duplicate_issue = forms.CharField(required=False)
	last_edited_at = forms.DateTimeField(required=False)
	
	def _get_reported_by_options(self):
		# Add organization groups and add all people in that organization to
		#		the given group.
		# Get all the reported by people and group them based on organization
		reported_by_keys = []
		reported_by_dict = {}
		for p in Person.objects.exclude(
				organization__type__type__icontains='archive').order_by(
						'last_name', 'first_name'):
			pkey = ' No Organization' if not p.organization else p.organization.name
			org_str = '' if not p.organization else ' (%s)'%p.organization.name
			if pkey not in reported_by_keys:
				reported_by_keys.append(pkey)
				reported_by_dict[pkey] = []
			reported_by_dict[pkey].append((p.pk, '%s, %s%s' % (p.last_name, p.first_name, org_str)))
			
		# After getting all the reported by groups, put them into a list in
		# alphabetical order
		reported_by_keys.sort()
		choices = [('', '')]
		for k in reported_by_keys:
			i = len(choices)
			if k:
				choices.append([k, []])
				for p in reported_by_dict[k]:
					choices[i][1].append(p)
			else:
				for p in reported_by_dict[k]:
					choices.append(p)
		return choices

	def __init__(self, vars={}, *args, **kwargs):
		user = vars.get('request__user', None)
		super(AddIssueForm, self).__init__(*args, **kwargs)
		nts = NoteType.objects.all().order_by('type')
		users_qs = Q(is_active=True)
		if self.instance and self.instance.assigned_to: 
			users_qs = users_qs | Q(pk=self.instance.assigned_to.pk)
		defaults = {
				'issue_priority': IssuePriority.objects.all().order_by('priority'),
				'assigned_to': User.objects.filter(users_qs, is_staff=True, 
					is_superuser=True).order_by('first_name', 'last_name', 'username'),				
				'note_type': get_note_types(user) if user else nts,
				'issue_project': IssueProject.objects.all().order_by('name'),
				}
		
		defaults['reported_by'] = self._get_reported_by_options()
		
		# Vars is a dict because helpers.render_data_page doesn't have a
		# very good way to pass variables into the form
		is_resolved = vars.get('is_resolved', None)

		self.fields['type_dis_relation'] = getIssueTypeDispositionRelationField(
				empty_labels=['', ''],
				field_names=['issue_type', 'issue_disposition'],
				table_headers=['Issue Type', 'Disposition'],
				is_resolved=is_resolved,
				required=[True, True],
				)
		
		self.fields['reported_by'] = forms.ChoiceField(
				choices=defaults.get('reported_by', []),
				required=False)
		self.fields['issue_priority'] = forms.ModelChoiceField(
				defaults.get('issue_priority', []),
				empty_label='')
		self.fields['assigned_to'] = forms.ModelChoiceField(
				defaults.get('assigned_to', []),
				empty_label='')
		self.fields['note_type'] = forms.ModelChoiceField(
				defaults.get('note_type', []),
				empty_label='')
		self.fields['issue_project'] = forms.ModelChoiceField(
				queryset=defaults.get('issue_project', []),
				empty_label='')
				
	def clean_reported_by(self):
		val = self.cleaned_data.get('reported_by', None)
		if not val:
			return None
		val = get_object_or_404(Person, pk=val)
		return val
	
	def save(self, commit=True):
		from app.templatetags.dicthandlers import getTicketsString
		m = super(AddIssueForm, self).save(commit=False)
		m.tickets = getTicketsString(m.tickets)
		m.save()
		return m

	class Meta:
		model = Issue


# ==============================================================================
class AddIssueFromNoteForm(ModelFormManager):

	resolved_date = forms.DateTimeField(required=False)
	title = commonCharField()
	tickets = forms.CharField(widget=forms.TextInput(attrs={'size': '52'}), 
			required=False)
	deadline = ZDateTimeField(required=False)
	duplicate_issue = forms.CharField(required=False)

	def __init__(self, *args, **kwargs):
		super(AddIssueFromNoteForm, self).__init__(*args, **kwargs)
		
		defaults = {
				'reported_by': Person.objects.exclude(
					organization__type__type__icontains='archive').order_by(
						'last_name', 'first_name'),
				'issue_priority': IssuePriority.objects.all().order_by('priority'),
				'assigned_to': User.objects.filter(is_active=True, 
					is_staff=True, is_superuser=True).order_by('first_name', 'last_name', 'username'),
				'note_type': NoteType.objects.all().order_by('type'),
				}

		self.fields['reported_by'] = forms.ModelChoiceField(
				defaults.get('reported_by', []),
				empty_label='',
				required=False)
		self.fields['issue_priority'] = forms.ModelChoiceField(
				defaults.get('issue_priority', []),
				empty_label='')
		self.fields['assigned_to'] = forms.ModelChoiceField(
				defaults.get('assigned_to', []),
				empty_label=None)
		self.fields['type_dis_relation'] = getIssueTypeDispositionRelationField(
				empty_labels=['', ''],
				field_names=['issue_type', 'issue_disposition'],
				table_headers=['Issue Type', 'Disposition'],
				is_resolved=False,
				required=[True, True]
				)

	class Meta:
		model = Issue


# ==============================================================================
class IssueInternalCommentAddForm(ModelFormManager):
	class Meta:
		model = IssueInternalComment


# ==============================================================================
class FeatureRequestPriorityFilterForm(forms.Form):
	filter_start_date = ZDateField(required=False)
	filter_end_date = ZDateField(required=False)
	filter_show_empties = forms.BooleanField(required=False)

# =============================================================================
class IssueFilterForm(forms.Form):

	filter_start_date = ZDateField(required=False)
	filter_end_date = ZDateField(required=False)
	filter_resolved_start_date = ZDateField(required=False)
	filter_resolved_end_date = ZDateField(required=False)
	filter_urgent = forms.BooleanField(required=False)
	filter_favorites = forms.BooleanField(required=False)
	filter_hide_comments = forms.BooleanField(required=False)
	filter_contains = forms.CharField(widget=forms.TextInput(attrs={'size': '55'}), required=False)

	STATUS_CHOICES = [
			('', 'All'),
			('1', 'Resolved'),
			('2', 'Unresolved'),
			('3', 'Upcoming Auto-Resolved'),
			]
	filter_status = forms.ChoiceField(choices=STATUS_CHOICES)

	ordering_choices = [('urgency', 'Handled/Unhandled'), 
			('deadline', 'Deadline'), 
#			('priority', 'Priority'),
			('issue date', 'Issue Date'),
			('favorites', 'Favorites'),
			]
	filter_order_by = forms.ChoiceField(choices=ordering_choices)

	def __init__(self, *args, **kwargs):
		super(IssueFilterForm, self).__init__(*args, **kwargs)
		self.fields['filter_areas'] = forms.MultipleChoiceField(
				choices=self.get_area_choices(), required=False)
		self.fields['filter_related_to'] = getRelatedToField()
		self.fields['filter_type_dis_relation'] = getIssueTypeDispositionRelationField(
				empty_labels=['All', 'All'], 
				field_names=['filter_issue_type', 'filter_disposition'],
				)
		self.fields['filter_issue_project'] = forms.ModelChoiceField(
				queryset=IssueProject.objects.all().order_by('name'),
				empty_label='All', required=False)

		self.fields['filter_assigned_to'] = forms.ModelChoiceField(
				User.objects.filter(is_active=True, 
					is_staff=True, is_superuser=True).order_by('first_name', 'last_name', 'username'),
				empty_label='All',
				required=False)
		self.fields['filter_created_by'] = forms.ModelChoiceField(
				User.objects.filter(is_active=True,
					is_staff=True, is_superuser=True).order_by('first_name', 'last_name', 'username'),
				empty_label='All', required=False)
#		self.fields['filter_priority'] = forms.ModelChoiceField(
#				IssuePriority.objects.all().order_by('priority'),
#				empty_label='All',
#				required=False)

	def get_area_choices(self):
		import olap_views
		token = olap_views.fb_login()
		areas = olap_views.fb_get_areas(token)
		olap_views.fb_logoff(token)
		return areas


# ==============================================================================
class IssueStatsFilterForm(forms.Form):

	def __init__(self, *args, **kwargs):
		super(IssueStatsFilterForm, self).__init__(*args, **kwargs)

		self.fields['filter_agency'] = forms.ModelChoiceField(
				Organization.unarchived_objects.all().order_by('name'),
				empty_label='All',
				required=False)
		self.fields['filter_project'] = forms.ModelChoiceField(
				IssueProject.objects.all().order_by('name'),
				required=False, empty_label='All')
		self.fields['filter_assigned_to'] = forms.ModelChoiceField(
				User.objects.filter(is_active=True, is_superuser=True).order_by('username'),
				required=False, empty_label='All')

# ==============================================================================
class IssueStatsByDateFilterForm(forms.Form):

	filter_show_fb = forms.BooleanField(required=False)
	filter_start_date = ZDateField(required=False)
	filter_end_date = ZDateField(required=False)

	def __init__(self, *args, **kwargs):
		super(IssueStatsByDateFilterForm, self).__init__(*args, **kwargs)

		self.fields['filter_agency'] = forms.ModelChoiceField(
				Organization.unarchived_objects.all().order_by('name'),
				empty_label='All',
				required=False)

# ==============================================================================
class ManageFeatureRequestsForm(forms.Form):

	def __init__(self, *args, **kwargs):
		super(ManageFeatureRequestsForm, self).__init__(*args, **kwargs)

		used_ids = []
		choices = [('', '')]
		for al in AgencyLogin.objects.all().order_by('user__first_name', 'user__last_name', 'user__username'):
			if al.user.pk not in used_ids:
				used_ids.append(al.user.pk)
				choices.append((al.user.pk, helpers.get_user_display(al.user)))
		self.fields['agency'] = forms.ChoiceField(choices=choices)


# ==============================================================================
# ============================== EMAILS ========================================
# ==============================================================================
class SendBulkEmailForm(forms.Form):

	subject = forms.CharField(widget=forms.TextInput(attrs={'size': '82'}))
	body = forms.CharField(widget=forms.Textarea(attrs={'cols': '85', 
		'rows':'15'}))

	def __init__(self, *args, **kwargs):
		super(SendBulkEmailForm, self).__init__(*args, **kwargs)
		versions = [('', '----------')]
		used_versions = []
		for i in Install.objects.all():
			# Doing this will make it so it doesn't query multiple times
			lia = i.latest_install_action
			# Get the amount of key contacts for the version
			clen = InstallKeyContact.objects.filter(install=i).count()
			if lia and clen > 0:
				v = lia.version
				if v not in used_versions:
					used_versions.append(v)
					versions.append((v, v))
		versions.sort()

		self.fields['versions'] = forms.ChoiceField(choices=versions, 
				required=False)

		self.fields['bulk_email_templates'] = forms.ModelChoiceField(
				queryset=BulkEmailTemplate.objects.all(),
				empty_label='-----------',
				required=False)


# ==============================================================================
class SendEmailForm(forms.Form):

	subject = forms.CharField(widget=forms.TextInput(attrs={'size': '50'}))
	body = forms.CharField(widget=forms.Textarea(attrs={'cols': '85', 
		'rows':'15'}))
	tickets = forms.CharField(widget=forms.TextInput(attrs={'size': '50'}),
			required=False)

	def __init__(self, is_resolved, tos=[], ccs=[], bccs=[], *args, **kwargs):
		super(SendEmailForm, self).__init__(*args, **kwargs)
		self.fields['to'] = forms.ChoiceField(choices=tos, required=False)
		self.fields['cc'] = forms.ChoiceField(choices=ccs, required=False)
		self.fields['bcc'] = forms.ChoiceField(choices=bccs, required=False)
		self.fields['type_dis_relation'] = getIssueTypeDispositionRelationField(
				empty_labels=['', ''],
				field_names=['issue_type', 'issue_disposition'],
				table_headers=['Issue Type', 'Disposition'],
				is_resolved=is_resolved,
				required=[True, True]
				)
	

# ==============================================================================
class MobileSendEmailForm(forms.Form):

	subject = forms.CharField(widget=forms.TextInput(attrs={'size': '47'}))
	body = forms.CharField(widget=forms.Textarea(attrs={'cols': '37', 
		'rows':'15'}))
	tickets = forms.CharField(widget=forms.TextInput(attrs={'size': '47'}),
			required=False)

	def __init__(self, is_resolved, tos=[], ccs=[], *args, **kwargs):
		super(MobileSendEmailForm, self).__init__(*args, **kwargs)
		self.fields['to'] = forms.ChoiceField(choices=tos, required=False)
		self.fields['cc'] = forms.ChoiceField(choices=ccs, required=False)
		self.fields['type_dis_relation'] = getIssueTypeDispositionRelationField(
				empty_labels=None,
				field_names=['issue_type', 'issue_disposition'],
				table_headers=['Issue Type', 'Disposition'],
				is_resolved=is_resolved,
				)

# ==============================================================================
class EmailAttachmentForm(ModelFormManager):
	original_name = commonCharField()

	class Meta:
		model = EmailAttachment

# ==============================================================================
class BulkEmailAttachmentForm(ModelFormManager):
	original_name = commonCharField()

	class Meta:
		model = BulkEmailAttachment

# ==============================================================================
class UnattachedEmailsForm(forms.Form):

	def __init__(self, *args, **kwargs):
		super(UnattachedEmailsForm, self).__init__(*args, **kwargs)

		self.fields['person'] = forms.ModelChoiceField(
				queryset=Person.objects.all(), empty_label='People')
		self.fields['organization'] = forms.ModelChoiceField(
				queryset=Organization.objects.all(), 
				empty_label='Organizations')
		self.fields['agency'] = forms.ModelChoiceField(
				queryset=User.objects.filter(is_active=True, is_staff=False,
					is_superuser=False), empty_label='Agencies')
		self.fields['admin'] = forms.ModelChoiceField(
				queryset=User.objects.filter(is_active=True, is_staff=True,
					is_superuser=True), empty_label='Admins')

# ==============================================================================
# ============================== SETTINGS ======================================
# ==============================================================================
class AddSettingAssignedDisposition(forms.Form):

	def __init__(self, *args, **kwargs):
		super(AddSettingAssignedDisposition, self).__init__(*args, **kwargs)
		
		ds = IssueDisposition.objects.filter(
				is_for_resolved=False).order_by('disposition')
		choices = [(d.pk, d.disposition) for d in ds]
		self.fields['default_assigned_disposition'] = forms.ChoiceField(
				choices=choices,
				required=True)


#===============================================================================
class AddSettingAssignedTo(forms.Form):

	def __init__(self, *args, **kwargs):
		super(AddSettingAssignedTo, self).__init__(*args, **kwargs)
		
		users = User.objects.filter(is_active=True, 
					is_staff=True, is_superuser=True).order_by('first_name', 'last_name', 'username')
		choices = [('', '')]
		choices.extend([(user.pk, helpers.get_user_display(user)) for user in users])

		self.fields['default_assigned_to'] = forms.ChoiceField(
				choices=choices,
				required=False)


# ==============================================================================
class AddSettingCreatedBy(forms.Form):

	def __init__(self, *args, **kwargs):
		super(AddSettingCreatedBy, self).__init__(*args, **kwargs)
		
		users = User.objects.filter(is_active=True, 
					is_staff=True, is_superuser=True).order_by('first_name', 'last_name', 'username')
		choices = [('', '')]
		choices.extend([(user.pk, helpers.get_user_display(user)) for user in users])

		self.fields['default_created_by'] = forms.ChoiceField(
				choices=choices,
				required=False)


# ==============================================================================
class AddSettingDemoAgency(forms.Form):

	def __init__(self, *args, **kwargs):
		super(AddSettingDemoAgency, self).__init__(*args, **kwargs)
		
		agencies = AgencyLogin.objects.all().order_by('organization__name')
		choices = [('', '')]
		choices.extend([(
			agency.pk, 
			'%s - %s' % (
				getFieldItem(agency, ['organization', 'name']),
				helpers.get_user_display(getFieldItem(agency, ['user']))
				)
			) for agency in agencies])

		self.fields['demo_agency'] = forms.ChoiceField(
				choices=choices,
				required=False)

# ==============================================================================
class AddSettingIssueAutoResponse(forms.Form):
			
	time_before_email = form_fields.ZDurationField(required=False, label='Response Interval')
	time_before_resolve = form_fields.ZDurationField(required=False, label='Resolve Interval')
	response_email_body = forms.CharField(required=True, widget=forms.Textarea(attrs={'rows':10, 'cols':100}))
	resolve_email_body = forms.CharField(required=True, widget=forms.Textarea(attrs={'rows':10, 'cols':100}))
	
	def __init__(self, *args, **kwargs):
		super(AddSettingIssueAutoResponse, self).__init__(*args, **kwargs)
		
		disposition_choices = [(disp.pk, disp.disposition) for disp in IssueDisposition.objects.filter(is_for_resolved=False).order_by('disposition')]
		resolved_disposition_choices = [(disp.pk, disp.disposition) for disp in IssueDisposition.objects.filter(is_for_resolved=True).order_by('disposition')]
		
		self.fields['disposition_list'] = forms.MultipleChoiceField(
				choices=disposition_choices, required=False)
		self.fields['resolve_disposition'] = forms.ChoiceField(
				choices=resolved_disposition_choices, required=True)
				
	def _get_intervals_from_clean_data(self):
		import re
		cleaned_data = self.cleaned_data
		
		response_interval = (cleaned_data.get('time_before_email', '') or '')
		response_interval_span = helpers.get_interval(response_interval)
		
		resolve_interval = (cleaned_data.get('time_before_resolve', '') or '')
		resolve_interval_span = helpers.get_interval(resolve_interval)
		
		return response_interval_span, resolve_interval_span
				
	def clean_time_before_email(self):
		response_interval, resolve_interval = self._get_intervals_from_clean_data()
		
		if response_interval and resolve_interval and (
				response_interval > resolve_interval):
			raise forms.ValidationError('The response interval needs to be before the resolve interval.')
		
		return self.cleaned_data.get('time_before_email')
				
	def clean_time_before_resolve(self):
		response_interval, resolve_interval = self._get_intervals_from_clean_data()
		
		if response_interval and resolve_interval and (
				resolve_interval < response_interval):
			raise forms.ValidationError('The resolve interval needs to be after the response interval.')
		
		return self.cleaned_data.get('time_before_resolve')
				
# ==============================================================================
# ============================== CALENDAR ======================================
# ==============================================================================
class CalendarForm(forms.Form):

	def __init__(self, request, *args, **kwargs):
		from datetime import datetime
		super(CalendarForm, self).__init__(*args, **kwargs)
		
		mo_names = {
				'1': 'Jan',
				'2': 'Feb',
				'3': 'Mar',
				'4': 'Apr',
				'5': 'May',
				'6': 'Jun',
				'7': 'Jul',
				'8': 'Aug',
				'9': 'Sept',
				'10': 'Oct',
				'11': 'Nov',
				'12': 'Dec',
				}
		
		users = User.objects.filter(is_active=True, 
					is_staff=True, is_superuser=True).order_by('first_name', 'last_name', 'username')
		choices = [('', 'All')]
		choices.extend([(user.pk, helpers.get_user_display(user)) for user in users])

		self.fields['users'] = forms.ChoiceField(choices=choices,
				required=False)

		event_models = []
		if permissions.isSupport(request.user):
			event_models.append([
				Issue.objects.exclude(deadline=None).order_by('-deadline'), 
				'deadline'])
		if permissions.isSales(request.user):
			event_models.append([
				Task.objects.exclude(deadline=None).order_by('-deadline'),
				'deadline'])
		event_models.append([WallboardEvent.objects.order_by('-event_date'), 
			'event_date'])
		
		yr_mos = {}
		used_mos = {}
		for i in event_models:
			for e in i[0]:
				yr = getFieldItem(e, [i[1], 'year'])
				mo = getFieldItem(e, [i[1], 'month'])
				mo_name = mo_names[str(mo)]
				yr_mos.setdefault(yr, [])
				used_mos.setdefault(yr, [])
				if mo not in used_mos[yr]:
					used_mos[yr].append(mo)
					yr_mos[yr].append((mo, mo_name))

		# Add the current year and month to the list for defaults
		now_yr = datetime.now().year
		now_mo = datetime.now().month
		now_mo_name = mo_names[str(now_mo)]
		yr_mos.setdefault(now_yr, [])
		used_mos.setdefault(now_yr, [])
		if now_mo not in used_mos[now_yr]:
			used_mos[now_yr].append(mo)
			yr_mos[now_yr].append((now_mo, now_mo_name))
			
		yr_choices = []
		for y, mlist in yr_mos.items():
			mlist.sort()
			yr_choices.append((y, y, mlist))

		yr_choices.sort()
		yr_choices.reverse()
		
		self.fields['year__month'] = RelationField(choices=yr_choices, 
				field_names=['year', 'month'], multi_line=False)


# ==============================================================================
# ================================ TASKS =======================================
# ==============================================================================
class AddTaskForm(ModelFormManager):

	deadline = ZDateTimeField(required=False)
	title = forms.CharField(widget=forms.TextInput(attrs={'size':'52'}))
	information = forms.CharField(widget=forms.Textarea(attrs={'size':'52'}), 
			required=False)

	class Meta:
		model = Task


# ==============================================================================
# ============================ OPPORTUNITIES ===================================
# ==============================================================================
class OpportunityFilterForm(forms.Form):

	p_choices = [
			('', 'All'),
			('10%', '10%'),
			('20%', '20%'),
			('30%', '30%'),
			('40%', '40%'),
			('50%', '50%'),
			('60%', '60%'),
			('70%', '70%'),
			('80%', '80%'),
			('90%', '90%'),		
			]

	c_choices = [
			('', 'All'),
			('Lead', 'Lead'),
			('Opportunity', 'Opportunity'),
			('Lost', 'Lost'),
			('Won', 'Won'),
			]

	st_choices = [('', 'All')] + list(STATE_CHOICES)

	category = forms.ChoiceField(choices=c_choices)
	percentage = forms.ChoiceField(choices=p_choices, required=False)
	contains = commonCharField()
	owner = forms.ModelChoiceField(
			User.objects.filter(is_active=True, is_staff=True, 
				is_superuser=True).order_by('first_name', 'last_name', 'username'),
			empty_label='All', required=False)
	state = forms.ChoiceField(choices=st_choices, required=False)

# ==============================================================================
class AddOpportunityForm(forms.Form):

	p_choices = [
			('', ''),
			('10%', '10%'),
			('20%', '20%'),
			('30%', '30%'),
			('40%', '40%'),
			('50%', '50%'),
			('60%', '60%'),
			('70%', '70%'),
			('80%', '80%'),
			('90%', '90%'),		
			]

	c_choices = [
			('', ''),
			('Lead', 'Lead'),
			('Opportunity', 'Opportunity'),
			('Lost', 'Lost'),
			('Won', 'Won'),
			]

	st_choices = [('', '')] + list(STATE_CHOICES)

	category = forms.ChoiceField(choices=c_choices)
	percentage = forms.ChoiceField(choices=p_choices, required=False)
	opportunity_source_comment = forms.CharField(widget=forms.Textarea, required=False)
	state = forms.ChoiceField(choices=st_choices, required=False)
	budgetary_amount = forms.DecimalField(required=False, decimal_places=2)
	actual_amount = forms.DecimalField(required=False, decimal_places=2)
	title = commonCharField()

	def __init__(self, *args, **kwargs):
		super(AddOpportunityForm, self).__init__(*args, **kwargs)

		self.fields['opportunity_source'] = forms.ModelChoiceField(
				OpportunitySource.objects.all(), required=False, empty_label='')

		self.fields['fiscal_period'] = forms.ModelChoiceField(
				FiscalPeriod.objects.all(), required=False, empty_label='')

		self.fields['owner'] = forms.ModelChoiceField(
				User.objects.filter(is_active=True, 
					is_staff=True, is_superuser=True).order_by('first_name', 'last_name', 
					'username'), required=False, empty_label='')
				
#		self.fields['stage'] = forms.ModelChoiceField(
#				OpportunityStage.objects.all(), required=False, empty_label='')
				
		self.fields['next_step'] = forms.ModelChoiceField(
				OpportunityNextStep.objects.all(), required=False, empty_label='')

		# Get the relation widget for people and orgs
		self.fields['related_to'] = getRelatedToField(
				field_names=['organizations', 'people'],
				table_headers=['Organization', 'Person'],
				empty_labels=[('', ''), ('', '')],
				required=[False, False],
				extras=[
					'''<a href="#" onclick="postExistingFormLinkID('/contacts/push/organization/', 'id_form'); return false;">
						<img src="/static/icons/icon_addlink.gif" />
					</a>''',
					'''<a href="#" onclick="postExistingFormLinkID('/contacts/push/person/', 'id_form'); return false;">
						<img class="imgLink" src="/static/icons/icon_addlink.gif" />
					</a>'''
					],
				show_all_orgs=True,
				)


# ==============================================================================
# ================================ CAMPAIGNS ===================================
# ==============================================================================
class CampaignFilterForm(forms.Form):
	choices = [
			('', 'All'),
			('True', 'Active'),
			('False', 'Inactive'),
			]
	status = forms.ChoiceField(choices=choices)

	
# ==============================================================================
class AddCampaignForm(ModelFormManager):

	name = commonCharField()
	is_active = forms.ChoiceField(choices=[(True, 'Active'), 
		(False, 'Inactive')])

	def __init__(self, *args, **kwargs):
		super(AddCampaignForm, self).__init__(*args, **kwargs)

		self.fields['owner'] = forms.ModelChoiceField(
				queryset=User.objects.filter(is_active=True, 
					is_staff=True, is_superuser=True).order_by('first_name', 'last_name', 'username'),
				empty_label='')

	class Meta:
		model = Campaign


# ==============================================================================
# ================================= INSTALLS ===================================
# ==============================================================================
class EditInstallKeyContactsForm(DynamicAddingForm):
	def __init__(self, fkey_model=None, *args, **kwargs):

		# Remove the instance keyword (this allows it to be a main form object
		# so it can be run through render_data_page)
		if kwargs.get('instance'):
			del kwargs['instance']

		# Get organizations related to the given Install
		try:
			pids = EmailAddress.objects.all().values_list(
					'person__pk', flat=True)
			choices = Person.objects.filter(pk__in=pids,
					organization__install__pk=fkey_model.pk)
		except:
			choices = Person.objects.none()

		field_opts = {
				'person': {
					'field': forms.ModelChoiceField,
					'kwargs': {
						'empty_label': '-------------------------',
						'queryset': choices,
						'label': 'Contact', 
						'required': True,
					},
				},
		}

		layout = [
				[['person']],
				]

		super(EditInstallKeyContactsForm, self).__init__(
				"EditInstallKeyContactsForm",
				'install', fkey_model, field_opts, 
				layout, SaveInstallKeyContacts, False, False,
				*args, **kwargs)

	class Meta:
		model = InstallKeyContact

# ==============================================================================
class AddInstallActionForm(ModelFormManager):
	action_date = ZDateTimeField()
	version = commonCharField()
	revision = commonCharField()
	action = commonCharField()
	taken_by = commonCharField()
	comments = forms.CharField(widget=forms.Textarea(
		attrs={'rows': '5', 'cols': '50'}), required=False)
	class Meta:
		model = InstallAction

# ==============================================================================
class InstallDatabaseGroupAddForm(ModelFormManager):
	name = commonCharField()
	class Meta:
		model = InstallDatabaseGroup

# ==============================================================================
class InstallEquipmentTypeAddForm(ModelFormManager):
	name = commonCharField()
	class Meta:
		model = InstallEquipmentType

#===============================================================================
class AddInstallEquipmentForm(DynamicAddingForm):
	def __init__(self, fkey_model=None, *args, **kwargs):
		
		fkey_name = fkey_model.__class__.__name__.lower()
	
		field_opts = {
				'name': {
					'field': commonCharField,
					'kwargs': {'label': 'Name', 'required': True},
				},
				'model_number': {
					'field': commonCharField,
					'kwargs': {'label': 'Model', 'required': False},
				},
				'quantity': {
					'field': forms.IntegerField,
					'kwargs': {'label': 'Quantity', 'required': False},
				},
				'item_type': {
					'field': forms.ModelChoiceField,
					'kwargs': {'label': 'Type', 'required': True,
						'queryset': InstallEquipmentType.objects.all(),
						'empty_label': None,
					},
				},
				'location': {
					'field': commonCharField,
					'kwargs': {'label': 'Location', 'required': False},
				},
				'information': {
					'field': commonTextarea,
					'kwargs': {'label': 'Information', 'required': False},
				},
		}

		layout = [
				[['name']], 
				[['model_number']],
				[['quantity']],
				[['item_type']],
				[['location']],
				[['information']],
				]

		super(AddInstallEquipmentForm, self).__init__("AddInstallEquipmentForm",
				fkey_name, fkey_model, field_opts, 
				layout, SaveInstallEquipment, True, True,
				*args, **kwargs)

	class Meta:
		model = InstallEquipment
		

#===============================================================================
class AddInstallInterfaceForm(DynamicAddingForm):
	def __init__(self, fkey_model=None, *args, **kwargs):
		
		fkey_name = fkey_model.__class__.__name__.lower()

		ic_queryset = Person.objects.all().order_by('last_name',
				'first_name')

		field_opts = {
				'name': {
					'field': commonCharField,
					'kwargs': {'label': 'Name', 'required': True},
				},
				'password': {
					'field': commonCharField,
					'kwargs': {'label': 'Password', 'required': False},
				},
				'contacts': {
					'field': form_fields.MultiComboboxSelectMultiple,
					'kwargs': {
						'queryset': ic_queryset, 'required': False, 
						'widget': forms.SelectMultiple, 'label': 'Contacts',
					},
				},
				'description': {
					'field': commonTextarea,
					'kwargs': {'label': 'Description', 'required': False},
				},
				'wiki_link': {
					'field': commonCharField,
					'kwargs': {'label': 'Wiki Link', 'required': False},
				},
				'url': {
					'field': commonCharField,
					'kwargs': {'label': 'Url', 'required': False},
				},
				'username': {
					'field': commonCharField,
					'kwargs': {'label': 'Username', 'required': False},
				},
				'information': {
					'field': commonTextarea,
					'kwargs': {'label': 'Information', 'required': False},
				},
		}

		layout = [
				[['name']], 
				[['username']],
				[['password']],
				[['url']],
				[['wiki_link']],
				[['contacts']],
				[['description']],
				[['information']],
				]

		super(AddInstallInterfaceForm, self).__init__("AddInstallInterfaceForm",
				fkey_name, fkey_model, field_opts, 
				layout, SaveInstallInterface, True, True,
				*args, **kwargs)

	class Meta:
		model = InstallInterface

# ==============================================================================
class InstallModuleAddForm(ModelFormManager):
	name = commonCharField()
	class Meta:
		model = InstallModule
		
#===============================================================================
class EditOrganizationInstallModuleForm(forms.Form):	
	def __init__(self, install_modules=[], *args, **kwargs):
		ret = super(EditOrganizationInstallModuleForm, self).__init__(*args, **kwargs)
		
		# Setup the datefield for each install module
		for im in install_modules:
			self.fields['%s_go_live_date' % im.pk] = ZDateTimeField(required=False, label=im.install_module.name)
		
		return ret	
		

#===============================================================================
class AddInstallServerForm(DynamicAddingForm):
	def __init__(self, fkey_model=None, *args, **kwargs):

		fkey_name = fkey_model.__class__.__name__.lower()
		field_opts = {
				'server_name': {
					'field': commonCharField,
					'kwargs': {'label': 'Server Name', 'required': True},
				},
				'alias': {
					'field': commonCharField,
					'kwargs': {'label': 'Alias', 'required': False},
				},
				'install_database_group': {
					'field': forms.ModelChoiceField,
					'kwargs': {'queryset': InstallDatabaseGroup.objects.all(),
						'required': False, 'label': 'Database Group',}
				},
				'role': {
					'field': commonCharField,
					'kwargs': {'label': 'Role', 'required': False},
				},
				'model': {
					'field': commonCharField,
					'kwargs': {'label': 'Model', 'required': False},
				},
				'location': {
					'field': commonCharField,
					'kwargs': {'label': 'Location', 'required': False},
				},
				'drac_ip': {
					'field': commonCharField,
					'kwargs': {'label': 'DRAC IP (LAN)', 'required': False},
				},
				'internal_ip': {
					'field': commonCharField,
					'kwargs': {'label': 'Internal IP (LAN)', 'required': False},
				},
				'external_ip': {
					'field': commonCharField,
					'kwargs': {'label': 'External IP (WAN)', 'required': False},
				},
				'mac_address': {
					'field': commonCharField,
					'kwargs': {'label': 'Mac Address', 'required': False},
				},
				'dns1': {
					'field': commonCharField,
					'kwargs': {'label': 'DNS 1', 'required': False},
				},
				'dns2': {
					'field': commonCharField,
					'kwargs': {'label': 'DNS 2', 'required': False},
				},
				'gateway': {
					'field': commonCharField,
					'kwargs': {'label': 'Gateway', 'required': False},
				},
				'subnet_mask': {
					'field': commonCharField,
					'kwargs': {'label': 'Subnet Mask', 'required': False},
				},
				'daemons': {
					'field': commonCharField,
					'kwargs': {'label': 'Daemons', 'required': False},
				},
				'express_service_code': {
					'field': commonCharField,
					'kwargs': {'label': 'Express Service Code', 
						'required': False,},
				},
				'service_tag': {
					'field': commonCharField,
					'kwargs': {'label': 'Service Tag', 'required': False,},
				},
				'hardware': {
					'field': commonTextarea,
					'kwargs': {'label': 'Hardware', 'required': False},
				},
				'monitor_link': {
					'field': commonCharField,
					'kwargs': {'label': 'Monitor Link', 'required': False},
				},
				'information': {
					'field': commonTextarea,
					'kwargs': {'label': 'Additional Info', 'required': False}
				},
		}

		layout = [
				[['server_name']], 
				[['alias']], 
				[['install_database_group']], 
				[['role']], 
				[['model']], 
				[['location']], 
				[['drac_ip']],
				[['internal_ip']],
				[['external_ip']],
				[['mac_address']],
				[['dns1']],
				[['dns2']],
				[['gateway']],
				[['subnet_mask']],
				[['daemons']],
				[['express_service_code']],
				[['service_tag']],
				[['hardware']],
				[['monitor_link']],
				[['information']],
				]

		super(AddInstallServerForm, self).__init__("AddInstallServerForm",
				fkey_name, fkey_model, field_opts, 
				layout, SaveInstallServer, True, True
				*args, **kwargs)
	
	class Meta:
		model = InstallServer	
		
		

#===============================================================================
class AddInstallForm(ModelFormManager):

	title = commonCharField()
	info = forms.CharField(widget=forms.Textarea(
		attrs={'rows': '10', 'cols': '50'}), required=False)

	go_live_date = ZDateTimeField(required=False)
	original_go_live_date = ZDateTimeField(required=False)

	physical_server_location = commonCharField(required=False)

	share1 = commonCharField(required=False)
	share2 = commonCharField(required=False)

	vpn_info = commonTextarea(required=False)
	install_contacts_other = forms.CharField(required=False, 
			widget=forms.Textarea(attrs={'rows': '5', 'cols': '50'}))

	recurring_reports = commonCharField(required=False)

	leds_email_settings = commonCharField(required=False)
	leds_email_username = commonCharField(required=False)
	leds_email_password = commonCharField(required=False)
	leds_email_smtp = commonCharField(required=False)

	gps = commonCharField(required=False)
	gizmo = commonCharField(required=False)
	inphoto_license = commonCharField(required=False)

	bullberry = commonCharField(required=False)
	bullberry_hostname = commonCharField(required=False)
	bullberry_username = commonCharField(required=False)
	bullberry_password = commonCharField(required=False)

	appriss = commonCharField(required=False)
	appriss_hostname = commonCharField(required=False)
	appriss_username = commonCharField(required=False)
	appriss_password = commonCharField(required=False)

	network_diagram = forms.ImageField(required=False)
	network_diagram_original_name = commonCharField(required=False)

	form_dependents = [AddInstallServerForm, AddInstallInterfaceForm]

	action_rel = commonCharField(required=False)
	taken_by_rel = commonCharField(required=False)
	version_rel = commonCharField(required=False)
	revision_rel = commonCharField(required=False)
	comments_rel = forms.CharField(required=False, widget=forms.Textarea(
		attrs={'rows': '5', 'cols': '50'}))

	def __init__(self, *args, **kwargs):
		super(AddInstallForm, self).__init__(*args, **kwargs)
		from datetime import datetime
		self.fields['action_date_rel'] = ZDateTimeField(initial=datetime.now(), required=False)
		self.fields['project_manager'] = forms.ModelChoiceField(queryset=User.objects.filter(is_staff=True, is_superuser=True, is_active=True).order_by('first_name', 'last_name', 'username'), required=False)
		
		ic_qs = Q(organization=None) | Q(organization__install=None)
		ic_queryset = Person.objects.exclude(ic_qs).order_by('last_name', 
				'first_name')
		self.fields['supervisor'] = forms.ModelChoiceField(queryset=ic_queryset,
				required=False)
		self.fields['install_contacts'] = form_fields.MultiComboboxSelectMultiple(queryset=ic_queryset, required=False, 
				widget=forms.SelectMultiple)

		self.fields['install_modules'] = form_fields.MultiComboboxSelectMultiple(
				queryset=InstallModule.objects.all().order_by('name'), 
				required=False, widget=forms.SelectMultiple)

	def is_valid(self, *args, **kwargs):
		valid = super(AddInstallForm, self).is_valid(*args, **kwargs)
		try:
			valid = clean_form_dependents(self) and valid
		except:
			import sys
			print 'ERROR VALIDATING addinstallform:', sys.exc_info()[0], sys.exc_info()[1]
			valid = False

		return valid
		
	def clean(self):
		cd = self.cleaned_data
		if cd.get('network_diagram', None):
			cd['network_diagram_original_name'] = str(cd['network_diagram'].name)
		elif 'network_diagram-clear' in self.data.keys():
			cd['network_diagram_original_name'] = ''
		elif self.instance and self.instance.network_diagram:
			cd['network_diagram_original_name'] = self.instance.network_diagram_original_name
		return cd

	class Meta:
		model = Install
				
# ==============================================================================
class InstallFilterForm(forms.Form):

	filter_contains = forms.CharField()

	def __init__(self, *args, **kwargs):
		super(InstallFilterForm, self).__init__(*args, **kwargs)		
		queryset=User.objects.filter(is_active=True, 
					is_staff=True, is_superuser=True).order_by('first_name', 'last_name', 'username')

		self.fields['filter_created_by'] = forms.ModelChoiceField(
				queryset=queryset,
				empty_label='All',
				required=False)

# ==============================================================================
# ============================ MANAGEMENT ======================================
# ==============================================================================
class DropdownForm(forms.Form):	

	def __init__(self, choices, *args, **kwargs):

		super(DropdownForm, self).__init__(*args, **kwargs)
		self.fields['edit'] = forms.ChoiceField(choices=choices, required=False)
		self.fields['remove'] = forms.ChoiceField(choices=choices, required=False)
		self.fields['original'] = forms.ChoiceField(choices=choices, required=False)
		self.fields['change'] = forms.ChoiceField(choices=choices, required=False)

	def clean(self):
		from django.forms.util import ValidationError
		cleaned = self.cleaned_data
		original = cleaned.get('original', '')
		change = cleaned.get('change', '')
		edit = cleaned.get('edit', '')
		remove = cleaned.get('remove', '')
		if original == change and not edit and not remove:
			raise ValidationError('Please choose two different items\nOriginal: %s\nChange:%s' % (original, change))
		return

# ==============================================================================
class BulkEmailTemplateAddForm(ModelFormManager):

	title = commonCharField()
	subject = commonCharField()
	
	body = forms.CharField(widget=forms.Textarea(attrs={'cols': '85', 
		'rows':'15'}))

	class Meta:
		model = BulkEmailTemplate

# ==============================================================================
class FiscalPeriodAddForm(ModelFormManager):

	q_choices = [
			['', ''],
			['Q1', 'Q1'],
			['Q2', 'Q2'],
			['Q3', 'Q3'],
			['Q4', 'Q4'],
			]

	y_choices = getYearChoices()
	
	quarter = forms.ChoiceField(choices=q_choices)
	year = forms.ChoiceField(choices=y_choices)

	class Meta:
		model = FiscalPeriod

# ==============================================================================
class IssueDispositionAddForm(ModelFormManager):
	disposition = commonCharField()

	class Meta:
		model = IssueDisposition

# ==============================================================================
class IssuePriorityAddForm(ModelFormManager):
	priority = commonCharField()

	class Meta:
		model = IssuePriority

# ==============================================================================
class IssueProjectAddForm(ModelFormManager):

	name = commonCharField()

	class Meta:
		model = IssueProject

# ==============================================================================
class IssueTypeAddForm(ModelFormManager):
	type = commonCharField()

	class Meta:
		model = IssueType

# ==============================================================================
class IssueTypeDispositionAddForm(ModelFormManager):

	def __init__(self, *args, **kwargs):
		super(IssueTypeDispositionAddForm, self).__init__(*args, **kwargs)
		self.fields['issue_type'] = forms.ModelChoiceField(
				queryset=IssueType.objects.all().order_by('type'),
				empty_label='----------')
		self.fields['issue_disposition'] = forms.ModelChoiceField(
				queryset=IssueDisposition.objects.all().order_by('disposition'),
				empty_label='----------')

	class Meta:
		model = IssueTypeDisposition

# ==============================================================================
class OpportunityNextStepAddForm(ModelFormManager):

	next_step = commonCharField()

	class Meta:
		model = OpportunityNextStep

# ==============================================================================
class OpportunitySourceAddForm(ModelFormManager):

	source = commonCharField()

	class Meta:
		model = OpportunitySource

# ==============================================================================
class OpportunityStageAddForm(ModelFormManager):

	stage = commonCharField()

	class Meta:
		model = OpportunityStage

# ==============================================================================
class OrganizationTypeAddForm(ModelFormManager):
	type = commonCharField()

	class Meta:
		model = OrganizationType

# ==============================================================================
class LogviewerLevelAddForm(ModelFormManager):

	class Meta:
		model = Level

# ==============================================================================
class LogviewerVersionAddForm(ModelFormManager):

	class Meta:
		model = Version

# ==============================================================================
class NoteCategoryAddForm(ModelFormManager):
	category = commonCharField()

	class Meta:
		model = NoteCategory

# ==============================================================================
class NoteTypeAddForm(ModelFormManager):
	type = commonCharField()

	class Meta:
		model = NoteType

# ==============================================================================
class PhoneTypeAddForm(ModelFormManager):
	type = commonCharField()

	class Meta:
		model = PhoneType

# ==============================================================================
class ProjectStatusAddForm(ModelFormManager):

	name = commonCharField()

	class Meta:
		model = ProjectStatus

# ==============================================================================
class ProjectTypeAddForm(ModelFormManager):

	name = commonCharField()

	class Meta:
		model = ProjectType

# ==============================================================================
class ReplyTypeAddForm(ModelFormManager):
	type = commonCharField()

	class Meta:
		model = ReplyType

# ==============================================================================
class TaskStatusAddForm(ModelFormManager):

	name = commonCharField()

	class Meta:
		model = TaskStatus

# ==============================================================================
class AgencyForm(forms.Form):

	def __init__(self, choices=[], *args, **kwargs):
		super(AgencyForm, self).__init__(*args, **kwargs)
		choices.insert(0, ('', ''))
		self.fields['agency'] = forms.ChoiceField(choices=choices)

# ==============================================================================
# =============================== LOGIN ========================================
# ==============================================================================
class AdminAddForm(forms.Form):

	password_change = forms.CharField(widget=forms.PasswordInput(), 
			required=False, initial='')
	password_confirmation = forms.CharField(widget=forms.PasswordInput(), 
			required=False, initial='')
	username = commonCharField(required=True)
	email = commonCharField(required=False)

	def __init__(self, current_user=None, is_admin=True, instance=None, *args, **kwargs):
		super(AdminAddForm, self).__init__(*args, **kwargs)
		self.current_user = current_user
		self.instance = instance or User()
		self.is_admin = is_admin
		if is_admin:
			self.fields['first_name'] = commonCharField(required=True)
			self.fields['last_name'] = commonCharField(required=True)

	def clean(self, *args, **kwargs):
		super(AdminAddForm, self).clean(*args, **kwargs)
		cd = self.cleaned_data
		pw1 = cd.get('password_change', '')
		pw2 = cd.get('password_confirmation', '')
		un = cd.get('username', '')
		
		username_is_changed = (un != self.instance.username)
		if self.instance and username_is_changed:
			from app.templatetags import permissions
			is_editing_self = (self.instance.pk != self.current_user.pk)
			is_superadmin = permissions.canDelete(self.current_user)
			# Can edit if no current user or if the user is an admin and is being
			# edited by him/her self
			if not is_superadmin and self.instance.pk and is_editing_self and self.is_admin:
				self.errors.setdefault('username', forms.util.ErrorList())
				self.errors['username'].append('You do not have permission to edit this field')
				
		if pw1 != pw2:
			self.errors.setdefault('password_change', forms.util.ErrorList())
			self.errors['password_change'].append('Password doesn\'t confirm')
			self.errors.setdefault('password_confirmation', 
					forms.util.ErrorList())
			self.errors['password_confirmation'].append(
					'Password doesn\'t confirm')
		return cd
	
	@transaction.commit_on_success
	def save(self, force_insert=False, force_update=False, commit=True):

		cleaned_data = self.data
		u = self.instance
		if not u:
			u = User()

		change_password = True
		_pw = cleaned_data.get('password_change', '')
		_pw2 = cleaned_data.get('password_confirmation', '')

		if len(_pw) == 0 or _pw != _pw2:
			change_password = False

		for f in cleaned_data:
			if 'password_change' in f and change_password:
				u.set_password(_pw)
			elif f in self.fields:
				setattr(u, f, cleaned_data[f])
				
		u.username = cleaned_data.get('username', '')		
		u.first_name = cleaned_data.get('first_name', '')
		u.last_name = cleaned_data.get('last_name', '')
		u.is_staff = cleaned_data.get('is_staff', True)
		u.is_superuser = cleaned_data.get('is_superuser', True)
		return u.save()


# ==============================================================================
# ================================= OLAP FORMS =================================
# ==============================================================================
class OlapDailyIssueFilterForm(forms.Form):

	olap_start = ZDateTimeField(required=False, label='Start Date')
	olap_end = ZDateTimeField(required=False, label='End Date')
	
	def __init__(self, is_resolved=False, *args, **kwargs):
		super(OlapDailyIssueFilterForm, self).__init__(*args, **kwargs)

		agencies = [['', 'All']]  + [[a.pk, helpers.get_user_display(a.user)] for a in AgencyLogin.objects.all().order_by('user__first_name', 'user__last_name', 'user__username')]

		self.fields['agency'] = forms.ChoiceField(choices=agencies, 
				required=False)
		self.fields['type_dis_relation'] = getIssueTypeDispositionRelationField(
				empty_labels=['All', 'All'],
				field_names = ['issue_type', 'issue_disposition'],
				table_headers = ['Issue type', 'Issue disposition'],
				required = [False, False],
				is_resolved=is_resolved,
				)

# ==============================================================================
class OlapIssueFilterForm(OlapDailyIssueFilterForm):
	
	def __init__(self, *args, **kwargs):
		super(OlapIssueFilterForm, self).__init__(*args, **kwargs)

		status = [['', 'All'], ['1', 'Resolved'], ['2', 'Unresolved']]
		self.fields['is_resolved'] = forms.ChoiceField(choices=status, 
				label='Status', required=False)

# ==============================================================================
# ============================== POWERLESS USERS ===============================
# ==============================================================================
class UserIssueFilterForm(forms.Form):

	filter_issue_id = forms.CharField(
			widget=forms.TextInput(attrs={'size': '4'}), required=False)
	filter_contains = forms.CharField(
			widget=forms.TextInput(attrs={'size': '35'}), required=False)
	filter_start_date = ZDateField(required=False)
	filter_end_date = ZDateField(required=False)

	choices_sel = [
			['', 'All'],
			['1', 'Resolved'],
			['2', 'Unresolved'],
			]

	filter_status = forms.ChoiceField(choices=choices_sel, required=False)

	def __init__(self, *args, **kwargs):
		super(UserIssueFilterForm, self).__init__(*args, **kwargs)

		self.fields['type_dis_relation'] = getIssueTypeDispositionRelationField(
				empty_labels=['All', 'All'],
				field_names = ['filter_type', 'filter_disposition'],
				table_headers = ['Type', 'Disposition'],
				required = [False, False],
				)
		
	
# ==============================================================================
# ================================== MISC ======================================
# ==============================================================================
class ErrorLogForm(forms.Form):
	show_warnings = forms.BooleanField(required=False)

#================================================================================
class EmailErrorLogForm(forms.Form):

	choices = [
			('', 'All'),
			('7', 'All - no errors'),
			('8', 'All - only errors'),
			('1', 'Received'),
			('2', 'Received - no errors'),
			('3', 'Received - only errors'),
			('4', 'Sent'),
			('5', 'Sent - no errors'),
			('6', 'Sent - only errors'),
			]
	status = forms.ChoiceField(choices=choices)	

# ==============================================================================
class URLForm(forms.Form):
	
	url = forms.URLField()
	def __init__(self, url='', *args, **kwargs):
		kwargs['url'] = url
		super(URLForm, self).__init__(*args, **kwargs)
		
	def clean_url(self):
		url = self.cleaned_data['url']
		url = url.replace(',', '')
		if '<' in url or '>' in url:
			raise forms.ValidationError('Url: [%s] is not valid' % url)
		return url


# ==============================================================================
# ========================== Install Save classes ==============================
# ==============================================================================
def SaveInstallKeyContacts(form, model, can_save=True):

	ikcs = []

	cd = form.dirty_data
	if not cd:
		# Try using just data
		cd = {}
		for d, v in form.data.items():
			cd[d] = v
	check_name = 'person'

	for i in form.get_indexes_from_form_data(check_name, cd):
		ikc = {}
		fields = [check_name]
		
		for f in fields:
			field_name = '%s_%s_%s' % (form.form_name, f, i)
			v = cd.get(field_name, None) or None
			if f == 'person' and v:
				## Make it a Person object
				v = get_object_or_404(Person, pk=v)
			ikc[f] = v
		if ikc:
			ikcs.append(ikc)

	return get_dynamic_form_field_actions(InstallKeyContact, 'install', model,
			ikcs, can_save=can_save)

# ==============================================================================
@transaction.commit_on_success
def SaveInstallServer(form, model, can_save=False):

	servers = []
	cd = form.cleaned_data or form.data
	check_name = 'server_name'
	
	for i in form.get_indexes_from_form_data(check_name, cd):
		server = {}
		fields = form.field_opts.keys()
		for f in fields:
			field_name = '%s_%s_%s' % (form.form_name, f, i)
			v = cd.get(field_name, None) or None
			if f.startswith('monitor_link') and v and not v.startswith('http'):
				v = 'http://%s' % v

			if 'install_database_group' == f and v:
				v = InstallDatabaseGroup.objects.get(pk=v)

			server[f] = v or None
		if server and server.get(check_name):
			servers.append(server)

	mname = model.__class__.__name__.lower()   # model name
	fkey_field_name = 'install'

	def edit_args_fn(m):		
		return {'server_name': m['server_name']}

	return get_dynamic_form_field_actions(InstallServer, fkey_field_name, model, 
			servers, can_save, edit_args_fn)

# ==============================================================================
@transaction.commit_on_success
def SaveInstallEquipment(form, model, can_save=False):

	equipments = []
	cd = form.dirty_data or form.data
	primary_field = 'name'
	
	for i in form.get_indexes_from_form_data(primary_field, cd):
		equipment = {}
		fields = form.field_opts.keys()
		
		for f in fields:
			# Don't process dependent fields in this section
			form_field = '%s_%s_%s' % (form.form_name, f, i)
			v = cd.get(form_field, None)

			if 'item_type' == f and v:
				v = InstallEquipmentType.objects.get(pk=v)
				
			equipment[f] = v or None
			
		if equipment:
			equipments.append(equipment)

	fkey_field_name = 'install'

	def edit_args_fn(m):
		return {'name': m['name']}

	return get_dynamic_form_field_actions(InstallEquipment, fkey_field_name, 
			model, equipments, can_save, edit_args_fn)

# ==============================================================================
@transaction.commit_on_success
def SaveInstallInterface(form, model, can_save=False):

	interfaces = []
	interface_contacts = []
	cd = form.dirty_data or form.data
	primary_field = 'name'
	
	for i in form.get_indexes_from_form_data(primary_field, cd):			
		interface = {'dependents': []}
		interfaces_contact = {}		
		fields = form.field_opts.keys()
		dependent_fields = ['contacts']
		
		for f in fields:
			# Don't process dependent fields in this section
			if f in dependent_fields: continue
			form_field = '%s_%s_%s' % (form.form_name, f, i)
			v = cd.get(form_field, None)
			interface[f] = v or None
			
		for f in dependent_fields:
			v = cd.getlist('%s_%s_%s'%(form.form_name,f,i))
			for c in v:
				interfaces_contact = {
						'contact': c,
						'model_class': InstallInterfacesContact,
						'fkey_field': 'install_interface',
						}
				interface['dependents'].append(interfaces_contact)
		if interface:
			interfaces.append(interface)

	mname = model.__class__.__name__.lower()   # model name
	fkey_field_name = 'install'

	def edit_args_fn(m):
		return {'name': m['name']}

	return get_dynamic_form_field_actions(InstallInterface, fkey_field_name, 
			model, interfaces, can_save, edit_args_fn)

# ==============================================================================
@transaction.commit_on_success
def SaveInstallContacts(request, model, can_save=False):

	ics = request.POST.getlist('install_contacts')
	install_contacts = [{'person': Person.objects.get(pk=c)} for c in ics]
	fkey_field_name = 'install'

	return get_dynamic_form_field_actions(InstallContact, fkey_field_name, 
			model, install_contacts, can_save, None)

# ==============================================================================
@transaction.commit_on_success
def SaveInstallModules(request, model, can_save=False):

	ims = request.POST.getlist('install_modules')
	install_modules = [{'install_module': InstallModule.objects.get(pk=m)} for m in ims]
	fkey_field_name = 'install'

	return get_dynamic_form_field_actions(InstallsInstallModule,
			fkey_field_name, model, install_modules, can_save, None)

# ==============================================================================
@transaction.commit_on_success
def get_dynamic_form_field_actions(model_class, fkey_field_name, fkey_model, 
	model_items, can_save=False, edit_args_fn=None):
	# model_items is a list of dictionaries containing field names and values
	# dependent_items is a list of dictionaries containing field names and values
	# 	for models that are dependent on model_items. Required keys are:
	# 	'model' and 'fkey_field'
	
	from app.management_views import delete_model
	
	# Don't delete/add any models that didn't change
	add_models = []
	ignore_model_ids = []
	edit_models = []
	deletes = []
	new_models = []
	dependents = {
			'deleted': {},  # The keys for these are the fkey models
			'added': {},
			'edited': {},
			}

	def _process_dependents(dependents, fkey_model, can_save):
		# dmodel_class and dfkey_field should be the same for all items in
		# the dependents list
		import copy

		dmodel_class = None
		dfkey_field = None
		ds = []
		dependents = copy.deepcopy(dependents)

		if not dependents:
			return [], [], []

		def _edit_args_fn(m):
			return {'contact': m['contact']}

		for d in dependents:
			dmodel_class = d['model_class']
			del d['model_class']
			dfkey_field = d['fkey_field']
			del d['fkey_field']

			for f, fm in getattr(dmodel_class(), 'dependent_fkey_fields', 
					{}).items():
				d[f] = get_object_or_404(fm, pk=d[f])				
				
			ds.append(d)

		ret = get_dynamic_form_field_actions(dmodel_class, dfkey_field, 
				fkey_model, ds, can_save, _edit_args_fn)[:3]
		return ret

	for m in model_items:
		## These require that the model be saved before they can be added
		ds = []
		if 'dependents' in m:
			ds = m['dependents']
			del m['dependents']

		## i_models include models that haven't changed
		i_models = model_class.objects.filter(
				Q(**{fkey_field_name: fkey_model.pk}), 
				Q(**m))
		i_model_ids = list(i_models.values_list('id', flat=True))
		## e_models include models that have changed anything except the values
		## in edit_args_fn
		if edit_args_fn:
			em_qs = Q(**{fkey_field_name: fkey_model.pk}) & Q(**edit_args_fn(m))
			e_models = model_class.objects.exclude(pk__in=i_model_ids).filter(
					em_qs)
		else:
			e_models = model_class.objects.none()

		if i_models.count() > 0:
			ignore_model_ids += i_model_ids
			non_ignore_model_ids = []
			# Check i_models for dependents
			if ds:
				for i_model in i_models:
					ds_info = list(_process_dependents(ds,i_model,can_save))
					if any(ds_info):
						non_ignore_model_ids.append(i_model.pk)

			if non_ignore_model_ids:
				em_qs = Q(Q(**{fkey_field_name: fkey_model.pk}),
						Q(**edit_args_fn(m))) | Q(pk__in=non_ignore_model_ids)
				e_models = model_class.objects.filter(em_qs)

		e_model_ids = list(e_models.values_list('id', flat=True))
		if e_models.count() > 0:
			# Get the changes for the edited models
			for e in e_models:
				ignore_list = e.dependent_ignore_fields

				old_model_instance, new_model = get_old_vals_and_new_model(
						m, e, ignore_list)
				edit_models.append([old_model_instance, new_model])
				ignore_model_ids.append(e.pk)
				
				# Handle the dependents here
				dlist = list(_process_dependents(ds, e, can_save))
				if any(dlist):
					dependents['deleted'][e], dependents['added'][e], dependents['edited'][e] = dlist
				
		elif not i_models:
			m['dependents'] = ds
			add_models.append(m)

	deletes_ids = []
	## Delete the address objects already connected to the person/org
	for pn in model_class.objects.exclude(pk__in=ignore_model_ids
			).filter(Q(**{fkey_field_name: fkey_model.pk})):
		deletes.append(pn)
		deletes_ids.append(pn.pk)
		if can_save:
			delete_model(pn.pk, None, model_class)

	# Save the edited models
	if can_save:
		for oes, nes in edit_models:
			nes.save()
			for d in dependents['deleted'].get(nes, []):
				delete_model(d.pk, None, d.__class__)
				
			for d in dependents['edited'].get(nes, []):
				d.save()
			
			for d in dependents['added'].get(nes, []):
				d.save()

	# Add the new models
	new_models = []
	new_models_ids = []
	for add in add_models:
		ds = []
		if 'dependents' in add:
			ds = add['dependents']
			del add['dependents']

		add[fkey_field_name] = fkey_model
		new_add = model_class(**add)

		if can_save:
			new_add.save()

		# Handle the dependents here
		dlist = list(_process_dependents(ds,new_add,can_save))
		if any(dlist):
			dependents['deleted'][new_add], dependents['added'][new_add], dependents['edited'][new_add] = dlist

		# Add the new model to the output
		new_models.append(new_add)


	# Save the dependents
	if can_save:
		for p, ds in dependents['deleted'].items():
			for d in ds:
				delete_model(d.pk, None, d.__class__)
		for p, ds in dependents['edited'].items():
			for d in ds:
				d.save()
		for p, ds in dependents['added'].items():
			for d in ds:
				d.save()

	return deletes, new_models, edit_models, dependents


# ==============================================================================
# ========================== Contact Save classes ==============================
# ==============================================================================
@transaction.commit_on_success
def SaveAddress(form, model, can_save=False):
	from app.management_views import delete_model
		
	addresses = []
	cd = form.dirty_data
	check_name = 'address'
	deletes = []
	new_addresses = []
	
	for i in form.get_indexes_from_form_data(check_name, cd):
		address = {}
		fields = ['address', 'state_province', 'city', 'zip']
		for f in fields:
			field_name = '%s_%s_%s' % (form.form_name, f, i)
			if cd.get(field_name, '').strip():
				address[f] = cd[field_name]
		if address:
			# Make all fields required
			addresses.append(address)

	# Delete the address objects already connected to the person/org
	mname = model.__class__.__name__.lower()   # model name
	for pn in Address.objects.filter(Q(**{mname: model.pk})):
		if can_save:
			delete_model(pn.pk, None, Address)
		else:
			deletes.append(pn)

	# Add the new addresses
	for add in addresses:
		new_add = Address(address=add.get('address', ''), 
				state_province=add.get('state_province', ''), 
				city=add.get('city', ''), zip=add.get('zip', ''))
		setattr(new_add, mname, model)
		if can_save:
			new_add.save()
		else:
			new_addresses.append(new_add)

	return deletes, new_addresses


# ==============================================================================
@transaction.commit_on_success
def SaveEmailAddress(form, model, can_save=False):
	from app.management_views import delete_model
	
	emails = []
	deletes = []
	news = []
	cd = form.dirty_data
	check_name = 'email'
	fn = form.form_name

	# Get all validated form data
	for i in form.get_indexes_from_form_data(check_name, cd):
		email = cd.get('%s_email_%s'%(fn, i), '').strip()
		is_primary = cd.get('%s_is_primary_%s'%(fn, i), '').strip().lower() == 'on'
		if email:
			emails.append([email, is_primary])

	# Delete the email address objects already connected to the person/org
	mname = model.__class__.__name__.lower()   # model name
	for e in EmailAddress.objects.filter(Q(**{mname: model.pk})):
		if can_save:
			delete_model(e.pk, None, EmailAddress)
		else:
			deletes.append(e)

	# Add the new email addresses
	for e in emails:
		new_email = EmailAddress(email=e[0], is_primary=e[1])
		setattr(new_email, mname, model)
		if can_save:
			new_email.save()
		else:
			news.append(new_email)

	return deletes, news


# ==============================================================================
@transaction.commit_on_success
def SavePhone(form, model, can_save=False):
	from app.management_views import delete_model
	
	numbers = []
	news = []
	deletes = []

	cd = form.dirty_data
	check_name = 'type'

	# Get all validated form data
	for i in form.get_indexes_from_form_data(check_name, cd):
		pt_ind = cd.get('%s_type_%s' % (form.form_name, i))
		pt = get_object_or_404(PhoneType, pk=pt_ind)
		number_val = cd.get('%s_number_%s'%(form.form_name, i), '').strip()
		if number_val:
			numbers.append([pt, number_val])

	# Delete the phone number objects already connected to the person/org
	mname = model.__class__.__name__.lower()   # model name
	for pn in PhoneNumber.objects.filter(Q(**{mname: model.pk})):
		if can_save:
			delete_model(pn.pk, None, PhoneNumber)
		else:
			deletes.append(pn)

	# Add the new numbers
	for num in numbers:
		new_pn = PhoneNumber(type=num[0], number=num[1])
		setattr(new_pn, mname, model)
		if can_save:
			new_pn.save()
		else:
			news.append(new_pn)
	return deletes, news

# ==============================================================================
def getIssueTypeDispositionRelationField(empty_labels=None, 
		field_names=[None, None], required=[True, True], table_headers=[],
		is_resolved=None, extras=['', '']):
	# empty_labels should be a list of two strings and an optional bool:
	#	[empty_val1, empty_val2 [, is_empty]] where:
	#	empty_val1 is the label for an empty value in the main field, 
	#	empty_val2 is the label for an empty value in the secondary field,
	#	is_empty will use an empty list for the secondary field value instead of a
	#	complete queryset when the main value is empty_val1. 

	# The empty_labels are mainly used for search pages to be able to search 
	#	"All" values

	# field_names is a two-item list of strings containing
	#	names for the field objects. The first item is the main
	#	input and the second is the relation input
	
	# is_resolved determines which dispositions will be displayed.
	#	Acceptable values are True, False and None. If None, it will display
	#	all values whether they are for resolved Issues or not.

	if empty_labels is None:
		empty_labels = [None, None, False]
	
	if len(empty_labels) < 3:
		empty_labels.append(False)
	elif empty_labels[0] is None:
		empty_labels[2] = False

	# --- Get choices for the issue type - disposition relation ---
	choices = []
	if len(empty_labels) > 0 and empty_labels[0] is not None:
		# If there is a valid empty label for the first item
		# then the user will be expecting to display all values
		ds = []
		if empty_labels[1] is not None:
			ds.append(("", empty_labels[1]))
		if len(empty_labels) != 3 or empty_labels[2] == False :
			issue_dispositions = IssueDisposition.objects.all().order_by('disposition')
			if is_resolved is not None:
				issue_dispositions = issue_dispositions.filter(is_for_resolved=is_resolved)
			for d in issue_dispositions:
				ds.append((d.pk, d.disposition))
		choices.append(("", empty_labels[0], ds))

	issue_type_list = list(IssueType.objects.all().order_by('type'))

	#Tell django to automatically fill in the issue_disposition object
	#otherwise loop below will call an individual select statement for each
	#instance of IssueTypeDisposition. See here for more info:
	#https://docs.djangoproject.com/en/dev/ref/models/querysets/#django.db.models.query.QuerySet.select_related
	itds = IssueTypeDisposition.objects.select_related('issue_disposition').filter(
			issue_type__in=[issue_type_obj.id for issue_type_obj in issue_type_list]
			).order_by('issue_disposition__disposition')

	if is_resolved is not None:
		itds = itds.filter(issue_disposition__is_for_resolved=is_resolved)

	for t in issue_type_list:
		# Get the dispositions
		ds = []
		if empty_labels and empty_labels[1] is not None:
			ds.append(("", empty_labels[1]))		
		for td in [type_disp for type_disp in itds if type_disp.issue_type_id == t.id]:
			# Disposition
			d = td.issue_disposition
			ds.append((d.pk, d.disposition))
		choices.append((t.pk, t.type, ds))

	return RelationField(choices=choices, field_names=field_names, 
			required=required, table_headers=table_headers, extras=extras)

# ==============================================================================
def getRelatedToField(field_names=[None, None], empty_labels=None,
		table_headers=[], required=[True, True], extras=['', ''],
		show_all_orgs=False):
	from string import capwords
	from app.models import __abr__

	if empty_labels is None or not empty_labels:
		empty_labels = [('all', 'All'), ('all', 'All')]
	
	demo_org = permissions.getDemoOrganization()
	person_qryset = None
	org_qryset = None

	# Don't allow users from demo org to show up
	if demo_org:
		person_qryset = Person.objects.exclude(organization__type__type__icontains='archive',organization=demo_org.pk).order_by(
						'last_name', 'first_name')
		org_qryset = Organization.unarchived_objects.exclude(pk=demo_org.pk).order_by('name')
	else:
		person_qryset = Person.objects.exclude(organization__type__type__icontains='archive').order_by(
						'last_name', 'first_name')		
		org_qryset = Organization.unarchived_objects.all().order_by('name')			

	people_list = list(person_qryset)	

	org_people_dict = {}

	if empty_labels[0]:
		# --- Get the choices for the Organization - Person relation field ---
		# All people for all organizations
		if empty_labels[1]:
			people = [(empty_labels[1][0], empty_labels[1][1])]
		else:
			people = []		
		for q in people_list:
			people.append((q.id, 
				__abr__('%s, %s' % (
				capwords(q.last_name).replace('"', "'"), 
				capwords(q.first_name).replace('"', "'")))))
			
			if q.organization_id not in org_people_dict:
				org_people_dict[q.organization_id] = list()
				
			org_people_list = org_people_dict.get(q.organization_id)
			org_people_list.append(q)	

		choices = [(empty_labels[0][0], empty_labels[0][1], people)]

	else:
		choices = []	

	for org in org_qryset:
		people_objs = []
		
		if org.pk in org_people_dict:
			people_objs = org_people_dict.get(org.pk) 	 	    

		if len(people_objs) > 0:
			if empty_labels[1]:
				people = [(empty_labels[1][0], empty_labels[1][1])]
			else:
				people = []
			for q in people_objs:
				people.append((q.id, __abr__('%s, %s' % (
					capwords(q.last_name).replace('"', "'"), 
					capwords(q.first_name).replace('"', "'")))))
			choices.append((org.pk, __abr__(capwords(org.name)), people))
		elif show_all_orgs:
			# This will show the org even though it has no people attached
			choices.append((org.pk, __abr__(capwords(org.name)), []))
	# ------------------------------------------------------

	return RelationField(field_names=field_names, choices=choices,
			table_headers=table_headers, required=required, extras=extras)


# ==============================================================================
# ================================= WALLBOARD ==================================
# ==============================================================================
class AddWallboardEventForm(ModelFormManager):
	title = commonCharField()
	event_date = ZDateTimeField()
	
	class Meta:
		model = WallboardEvent

# ==============================================================================
class AddWallboardNoteForm(ModelFormManager):

	note = forms.CharField(widget=forms.Textarea(attrs={
		'rows': '3', 'cols': '50'}))

	class Meta:
		model = WallboardNote


