# vim: set ts=4 sw=4 et: coding=UTF-8

#
# Copyright (c) 2011, Novell, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#  * Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#  * Neither the name of the <ORGANIZATION> nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
#
# (Licensed under the simplified BSD license)
#
# Authors: Vincent Untz <vuntz@opensuse.org>
#

import os
import sys

import cgi
import errno
import operator
import optparse
import re
import traceback
import urllib
import urllib2

import osc_copy
from util import safe_mkdir_p

try:
    from lxml import etree as ET
except ImportError:
    try:
        from xml.etree import cElementTree as ET
    except ImportError:
        import cElementTree as ET


NO_DEVEL_PACKAGE_SAFE_REGEXP = [ '^_product.*' ]


#######################################################################


NO_DEVEL_PACKAGE_SAFE_REGEXP = [ re.compile(x) for x in NO_DEVEL_PACKAGE_SAFE_REGEXP ]


#######################################################################


class AutoSubmitException(Exception):
    pass

class AutoSubmitUnlikelyException(AutoSubmitException):
    pass

#######################################################################


class AutoSubmitConfig:

    def __init__(self, options):
        self.cache_dir = options.cache_dir or os.getcwd()
        self.apiurl = options.apiurl or 'https://api.opensuse.org/'
        self.project = options.project or 'openSUSE:Factory'
        self.verbose = options.verbose
        self.debug = options.debug


#######################################################################


def fetch_status_for_project(apiurl, project):
    return ET.parse('/tmp/vuntz-random/factory-status.xml').getroot()
    url = osc_copy.makeurl(apiurl, ['status', 'project', project])

    try:
        fin = http_GET(url)
    except urllib2.HTTPError, e:
        raise AutoSubmitException('Cannot get status of %s: %s' % (project, e))

    try:
        node = ET.parse(fin).getroot()
    except SyntaxError, e:
        fin.close()
        raise AutoSubmitException('Cannot parse status of %s: %s' % (project, e))

    fin.close()

    return node


#######################################################################


def fetch_search_results(apiurl, xpath):
    return ET.parse('/tmp/vuntz-random/factory-requests.xml').getroot()
    url = osc_copy.makeurl(apiurl, ['search', 'request'], ['match=%s' % urllib.quote_plus(xpath)])

    try:
        fin = http_GET(url)
    except urllib2.HTTPError, e:
        raise AutoSubmitException('Cannot get search results: %s' % e)

    try:
        node = ET.parse(fin).getroot()
    except SyntaxError, e:
        fin.close()
        raise AutoSubmitException('Cannot parse search results: %s' % e)

    fin.close()

    return node


#######################################################################


def fetch_package_files_metadata(apiurl, project, package, revision):
    query = None
    if revision:
        query = { 'rev': revision }
    url = osc_copy.makeurl(apiurl, ['public', 'source', project, package], query=query)

    try:
        fin = http_GET(url)
    except urllib2.HTTPError, e:
        raise AutoSubmitException('Cannot get files metadata of %s/%s: %s' % (project, package, e))

    try:
        node = ET.parse(fin).getroot()
    except SyntaxError, e:
        fin.close()
        raise AutoSubmitException('Cannot parse files metadata of %s/%s: %s' % (project, package, e))

    fin.close()

    return node


#######################################################################


def create_submit_request(apiurl, source_project, source_package, target_project, target_package):
#TODO get rev!
    return

    request = ET.Element('request')
    request.set('type', 'submit')

    submit = ET.SubElement(request, 'submit')

    source = ET.SubElement(submit, 'source')
    source.set('project', source_project)
    source.set('package', source_package)
    source.set('rev', rev)

    target = ET.SubElement(submit, 'target')
    target.set('project', target_project)
    target.set('package', target_package)

    state = ET.SubElement(request, 'state')
    state.set('name', 'new')

    description = ET.SubElement(request, 'description')
    description.text = 'Automatically submitted by auto-submit script'

    tree = ET.ElementTree(request)
    xml = ET.tostring(tree)

    url = osc_copy.makeurl(apiurl, ['request'], query='cmd=create')

    try:
        fin = http_POST(url, data=xml)
    except urllib2.HTTPError, e:
        raise AutoSubmitException('Cannot submit %s to %s: %s' % (source_project, source_package, target_project, target_package, e))

    try:
        node = ET.parse(fin).getroot()
    except SyntaxError, e:
        fin.close()
        raise AutoSubmitException('Cannot parse result of submission of %s to %s: %s' % (source_project, source_package, target_project, target_package, e))

    fin.close()


#######################################################################


class AutoSubmitPackage:

    def __init__(self, project, package, state_hash = '', unexpanded_state_hash = '', rev = '', changes_hash = ''):
        self.project = project
        self.package = package
        self.state_hash = state_hash
        self.unexpanded_state_hash = unexpanded_state_hash
        self.rev = rev
        self.changes_hash = changes_hash


    def set_state_from_files_metadata(self, directory_node):
        linkinfo_node = directory_node.find('linkinfo')
        if linkinfo_node is None:
            # not a link
            self.expanded_state_hash = linkinfo_node.get('srcmd5')
        else:
            # really a link; we'll only have the unexpanded state
            self.unexpanded_state_hash = linkinfo_node.get('xsrcmd5')


    @classmethod
    def from_status_node(cls, node):
        project = node.get('project')
        package = node.get('name')
        unexpanded_md5 = node.get('srcmd5')
        expanded_md5 = node.get('verifymd5') or unexpanded_md5
        changes_md5 = node.get('changesmd5')

        if not expanded_md5:
            raise AutoSubmitUnlikelyException('no state hash for %s/%s (?)' % (project, package))

        return AutoSubmitPackage(project, package, state_hash = expanded_md5, unexpanded_state_hash = unexpanded_md5, changes_hash = changes_md5)


    @classmethod
    def from_status_develpack_node(cls, develpack_node):
        project = develpack_node.get('proj')
        package = develpack_node.get('pack')

        package_node = develpack_node.find('package')
        if package_node is not None:
            ret = AutoSubmitPackage.from_status_node(package_node)
            if ret.project == project and ret.package == package:
                return ret
            else:
                raise AutoSubmitUnlikelyException('inconsistent develpack and package nodes')
        else:
            raise AutoSubmitUnlikelyException('no package node in develpack node')


    @classmethod
    def from_request_node(cls, node):
        project = node.get('project')
        package = node.get('package')
        rev = node.get('rev')

        return AutoSubmitPackage(project, package, rev = rev)


    # Note: we do not use the state hash below; this makes it easy to know
    # if we're looking at the same package even if its content is different

    def __eq__(self, other):
        return self.project == other.project and self.package == other.package

    def __ne__(self, other):
        return not self.__eq__(other)

    def __lt__(self, other):
        return str(self) < str(other)

    def __le__(self, other):
        return self.__eq__(other) or self.__lt__(other)

    def __gt__(self, other):
        return other.__lt__(self)

    def __ge__(self, other):
        return other.__eq__(self) or other.__lt__(self)

    def __str__(self):
        return '%s/%s' % (self.project, self.package)


#######################################################################


class AutoSubmitWorker:

    def __init__(self, conf):
        self.conf = conf


    def _verbose_print(self, s, level = 1):
        if self.conf.verbose >= level:
            print s


    def _fetch_packages_with_diff(self):
        xml_root = fetch_status_for_project(self.conf.apiurl, self.conf.project)

        with_diff_hash = {}
        self._packages_with_diff = []

        for package_node in xml_root.findall('package'):
            try:
                parent_package = AutoSubmitPackage.from_status_node(package_node)
            except AutoSubmitUnlikelyException, e: 
                print >>sys.stderr, 'Cannot get package: %s' % e
                continue

            if parent_package.project != self.conf.project:
                print >>sys.stderr, '%s found as parent package while auto-submitting to %s.' % (parent_package, self.conf.project)
                continue

            develpack_node = package_node.find('develpack')
            if develpack_node is None:
                safe = False
                for regexp in NO_DEVEL_PACKAGE_SAFE_REGEXP:
                    match = regexp.match(parent_package.package)
                    if match:
                        safe = True
                        break

                if not safe:
                    print >>sys.stderr, 'No devel package for %s.' % parent_package
                continue

            try:
                devel_package = AutoSubmitPackage.from_status_develpack_node(develpack_node)
            except AutoSubmitUnlikelyException, e: 
                print >>sys.stderr, 'Cannot get devel package for %s: %s' % (parent_package, e)
                continue

            if parent_package.state_hash == devel_package.state_hash:
                continue

            if devel_package.project == self.conf.project:
                print >>sys.stderr, "Devel package %s (for %s) belongs to target project %s but state hash is different: this should never happen." % (devel_package, parent_package, self.conf.project)
                continue

            hash_key = str(parent_package)
            if with_diff_hash.has_key(hash_key):
                print >>sys.stderr, '%s appearing twice as parent package.' % parent_package
                continue

            with_diff_hash[hash_key] = True

            self._packages_with_diff.append((devel_package, parent_package))

        self._packages_with_diff.sort()


    def _read_cache(self):
        self._cache = {}
#TODO


    def _write_cache(self):
        ''' The cache only contains packages with a difference as of now, with
            each of them belonging to one category:
              - the list of packages that we filtered (since they should stay
                ignored).
              - the list of packages that we successfully submitted (since they
                should be ignored now).
            We need to put the second category in there, else we'll rely on the
            "look at new submit requests" filter, which requires some traffic.
        '''
#TODO
        for (devel_package, parent_package) in self._packages_filtered:
            pass
        for (devel_package, parent_package) in self._packages_submitted:
            pass


    def _fetch_package_state_at_rev(self, package):
        ''' Gets the state hash of a package for a specific revision.

            This is only needed when we don't already have a state hash, ie
            when we got a package from a request.
            
            Note that, to avoid unneeded traffic, we get only the unexpanded
            state hash if it's a link.
        '''
#TODO disabled for now
        return

        if not package.rev:
            # we already fetched the state (or this should not have been called at all)
            return

        if package.state_hash or package.unexpanded_state_hash:
            raise AutoSubmitException('Fetching state of %s while we already have the state.' % package)

        xml_root = fetch_package_files_metadata(self.conf.apiurl, package.project, package.package, package.rev)
        package.set_state_from_files_metadata(xml_root)

        # Reset revision: we don't need it anymore
        self.rev = ''


    def _fetch_requests_since_last_run(self):
        ''' If we run for the first time, we just get the list of open requests.
            Those will be the base of what we know we can ignore for auto-submit.

            If we already run before, then we get the list of all requests since
            the last run (we kept the timestamp of the most recent request). Those
            new requests let us know what we should not auto-submit (even if a
            request was revoked or superseded -- since that means the source that
            got revoked/superseded should not be pushed).

            In all cases, we will only auto-submit if the current state of the
            source is different from the state when the request was created.

        '''
#FIXME: admittedly, there's a race here: between the time where we get this
# list, and the time where we use it to check if a package should be submitted,
# new requests might have been created. Let's say it's good enough for now,
# though.

        if self._most_recent_request:
            restrict_xpath = '(compare(state/@when,\'%s\') >= 0)' % self._most_recent_request
        else:
            restrict_xpath = 'state/@name=\'new\' or state/@name=\'review\''

        xpath_base = '(action/@type=\'submit\' or action/@type=\'delete\') and (action/target/@project=\'%(project)s\' or submit/target/@project=\'%(project)s\')' % { 'project': self.conf.project }

        xpath = '(%s) and (%s)' % (restrict_xpath, xpath_base)

        xml_root = fetch_search_results(self.conf.apiurl, xpath)

        self._delete_requests = {}
        self._submit_requests = {}

        for request_node in xml_root.findall('request'):
            state_node = request_node.find('state')
            if state_node is not None:
                state = state_node.get('name')
                when = state_node.get('when')
                if when > self._most_recent_request:
                    self._most_recent_request = when
            else:
                raise AutoSubmitUnlikelyException('no state for request %s' % request_node.get('id'))

            for action_node in request_node.findall('action'):
                source = None
                target = None

                source_node = action_node.find('source')
                if source_node is not None:
                    source = AutoSubmitPackage.from_request_node(source_node)

                target_node = action_node.find('target')
                if target_node is not None:
                    target = AutoSubmitPackage.from_request_node(target_node)

                if action_node.get('type') == 'delete':
                    if state in ['new', 'review', 'accepted']:
                        self._delete_requests[str(target)] = True
                elif action_node.get('type') == 'submit':
                    if self._submit_requests.has_key(str(target)):
                        sources = self._submit_requests[str(target)]
                    else:
                        sources = []
                    sources.append(source)
                    self._submit_requests[str(target)] = sources


    def _filter_packages_to_submit(self):
        ''' To know if we need to create a submit request, we check the
            following:
            a) Checks that do not require any network activity
               1) the current state of the devel package is not a state we've
                  already seen
               2) there is a diff in .changes between devel and parent
               3) the parent package has no deleterequest associated to it (we
                  have the list of requests already)
            b) Checks that do require network activity
               1) there has been no submit request (even if revoked,
                  superseded, etc.) with the same state in the devel package
                  since our last run (we have the list of requests already, but
                  we do not have the state of the sources of the requests)
               2) auto-submit is enabled for the devel package (or for the
                  whole devel project)
               3) state of the devel package we got via the status API is still
                  current; if no, go back to a) with up-to-date state
        '''
        self._packages_to_submit = []

        for (devel_package, parent_package) in self._packages_with_diff:
            # a.1. Was already seen/handled in the past; this cache is not completely useless! ;-)
            try:
#TODO: we'll likely have a different structure in the cache, especially as we do not necessarily have the expanded state hash for submitted packages
                (last_submitted_package, last_seen_state_hash) = self._cache[str(parent_package)]
                if last_submitted_package == devel_package and (devel_package.state_hash in [last_submitted_package.state_hash, last_seen_state_hash]):
                    self._verbose_print("Not submitting %s to %s: already seen in the past." % (devel_package, parent_package))
                    continue
            except KeyError:
                pass

            # a.2. The .changes files are the same, so not worth submitting.
            # (Ignored if the status API doesn't have the attributes for .changes hash)
            if parent_package.changes_hash and parent_package.changes_hash == devel_package.changes_hash:
                self._verbose_print("Not submitting %s to %s: .changes files are the same." % (devel_package, parent_package))
                continue

            # a.3. The parent package is, apparently, scheduled to be deleted. If the delete request is rejected, we'll submit on next run anyway.
            if self._delete_requests.has_key(str(parent_package)):
                self._verbose_print("Not submitting %s to %s: delete request for %s filed." % (devel_package, parent_package, parent_package))
                continue

            # b.1. There is already a submit request; we need to see if this is for the current state of the source package.
            if self._submit_requests.has_key(str(parent_package)):
                ignore = False
                sources = self._submit_requests[str(parent_package)]
                for source in sources:
                    if devel_package != source:
                        continue

                    self._fetch_package_state_at_rev(source)

                    if (source.state_hash and source.state_hash == devel_package.state_hash):
                        ignore = True
                    if (source.unexpanded_state_hash and source.unexpanded_state_hash == devel_package.unexpanded_state_hash):
                        ignore = True
                    if ignore:
                        break

                if ignore:
                    self._verbose_print("Not submitting %s to %s: already submitted." % (devel_package, parent_package))
                    continue
                else:
                    self._verbose_print("Should submit %s to %s with newer version." % (devel_package, parent_package))

#TODO b.2.
#TODO b.3.

            self._packages_to_submit.append((devel_package, parent_package))

        self._packages_filtered = [ x for x in self._packages_to_submit if x not in self._packages_to_submit ]


    def _do_auto_submit(self):
        self._packages_submitted = []

        for (devel_package, parent_package) in self._packages_to_submit:
            self._verbose_print("Submitting %s to %s" % (devel_package, parent_package), level = 2)
            try:
                print "DEBUG TO REMOVE: Submitting %s to %s" % (devel_package, parent_package)
                create_submit_request(self.conf.apiurl, devel_package.project, devel_package.package, parent_package.project, parent_package.package)
                self._packages_submitted.append((devel_package, parent_package))
            except Exception, e:
                print >>sys.stderr, 'Failed to submit %s to %s: %s' (devel_package, parent_package, e)


    def run(self):
        self._most_recent_request = ''

        self._fetch_packages_with_diff()
        self._fetch_requests_since_last_run()

        self._read_cache()
        self._filter_packages_to_submit()
        self._do_auto_submit()
        self._write_cache()


# Keep in status:
# time of last sr seen with request_list (not the ones we generated); if empty, means there is no cache
# project that the cache is being used for (so we can know the cache should be ignored if it's different)

#TODO after doing all submits, we should:
# a) put in our cache the info for this submit (source, target, md5 of source)
# b) put in our cache the info of other existing sr (so we don't auto-submit them later on -- that could happen if a sr was sent by somebody else, rejected and then found by the script again). Only put info about the last sr we know about (either seen, or autosubmitted) + md5 as seen in status (=> helps avoid the case where status doesn't get updated; we know we'll have handled it)


#######################################################################


def lock_run(conf):
    # FIXME: this is racy, we need a real lock file. Or use an atomic operation
    # like mkdir instead
    running_file = os.path.join(conf.cache_dir, 'running')

    if os.path.exists(running_file):
        return False

    open(running_file, 'w').write('')

    return True


def unlock_run(conf):
    running_file = os.path.join(conf.cache_dir, 'running')

    os.unlink(running_file)


#######################################################################


def main(args):
    parser = optparse.OptionParser()

    parser.add_option('--cache-dir', dest='cache_dir',
                      help='cache directory (default: current directory)')
    parser.add_option('--apiurl', '-A', dest='apiurl', default='https://api.opensuse.org/',
                      help='build service API server (default: https://api.opensuse.org/)')
    parser.add_option('--project', '-p', dest='project', default='openSUSE:Factory',
                      help='target project to auto-submit to (default: openSUSE:Factory)')
    parser.add_option('--log', dest='log',
                      help='log file to use (default: stderr)')
    parser.add_option('--verbose', '-v', action='count',
                      default=0, dest='verbose',
                      help='be verbose; use multiple times to add more verbosity (default: false)')
    parser.add_option('--debug', action='store_true',
                      default=False, dest='debug',
                      help='add debug output and do not create real submit requests (default: false)')

    (options, args) = parser.parse_args()

    conf = AutoSubmitConfig(options)

    if options.log:
        path = os.path.realpath(options.log)
        safe_mkdir_p(os.path.dirname(path))
        sys.stderr = open(options.log, 'a')

    try:
        os.makedirs(conf.cache_dir)
    except OSError, e:
        if e.errno != errno.EEXIST:
            print >>sys.stderr, 'Cannot create cache directory: %s' % e
            return 1

    if not lock_run(conf):
        print >>sys.stderr, 'Another instance of the script is running.'
        return 1

    worker = AutoSubmitWorker(conf)

    retval = 1

    try:
        worker.run()
        retval = 0
    except Exception, e:
        if isinstance(e, (AutoSubmitException,)):
            print >>sys.stderr, e
        else:
            traceback.print_exc()

    unlock_run(conf)

    return retval


if __name__ == '__main__':
    try:
        ret = main(sys.argv)
        sys.exit(ret)
    except KeyboardInterrupt:
        pass
