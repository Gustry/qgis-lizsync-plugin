"""
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

__author__ = '3liz'
__date__ = '2018-12-19'
__copyright__ = '(C) 2018 by 3liz'

import os

from qgis.core import (
    QgsProcessingException,
    QgsProcessingParameterString,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFile,
    QgsProcessingOutputString,
    QgsProcessingOutputNumber
)

from ftplib import FTP
from .tools import (
    checkFtpBinary,
    check_ftp_connection,
    ftp_sync,
    get_ftp_password,
    lizsyncConfig,
)
from ...qgis_plugin_tools.tools.i18n import tr
from ...qgis_plugin_tools.tools.algorithm_processing import BaseProcessingAlgorithm


class SendProjectsAndFilesToCloneFtp(BaseProcessingAlgorithm):
    """
    Synchronize local data from remote FTP
    via LFTP
    """

    # Constants used to refer to parameters and outputs. They will be
    # used when calling the algorithm from another algorithm, or when
    # calling from the QGIS console.

    CONNECTION_NAME_CENTRAL = 'CONNECTION_NAME_CENTRAL'
    LOCAL_QGIS_PROJECT_FOLDER = 'LOCAL_QGIS_PROJECT_FOLDER'

    CLONE_FTP_HOST = 'CLONE_FTP_HOST'
    CLONE_FTP_PORT = 'CLONE_FTP_PORT'
    CLONE_FTP_LOGIN = 'CLONE_FTP_LOGIN'
    CLONE_FTP_PASSWORD = 'CLONE_FTP_PASSWORD'
    CLONE_FTP_REMOTE_DIR = 'CLONE_FTP_REMOTE_DIR'
    CLONE_QGIS_PROJECT_FOLDER = 'CLONE_QGIS_PROJECT_FOLDER'

    FTP_EXCLUDE_REMOTE_SUBDIRS = 'FTP_EXCLUDE_REMOTE_SUBDIRS'

    OUTPUT_STATUS = 'OUTPUT_STATUS'
    OUTPUT_STRING = 'OUTPUT_STRING'

    def name(self):
        return 'send_projects_and_files_to_clone_ftp'

    def displayName(self):
        return tr('Send local QGIS projects and files to the clone FTP server')

    def group(self):
        return tr('03 GeoPoppy file synchronization')

    def groupId(self):
        return 'lizsync_geopoppy_sync'

    def shortHelpString(self):
        short_help = tr(
            ' Send QGIS projects and files to the clone FTP server remote directory.'
            '\n'
            '\n'
            ' This script can be used by the geomatician in charge of the deployment of data'
            ' to one or several clone(s).'
            '\n'
            '\n'
            ' It synchronizes the files from the given local QGIS project folder'
            ' to the clone remote folder by using the given FTP connexion.'
            ' This means all the files from the clone folder will be overwritten'
            ' by the files from the local QGIS project folder.'
            '\n'
            '\n'
            ' Beware ! This script does not adapt projects for the clone database'
            ' (no modification of the PostgreSQL connexion data inside the QGIS project files) !'
        )
        return short_help

    def initAlgorithm(self, config):
        """
        Here we define the inputs and output of the algorithm, along
        with some other properties.
        """
        # LizSync config file from ini
        ls = lizsyncConfig()

        # INPUTS

        # Central database connection name
        connection_name_central = ls.variable('postgresql:central/name')
        db_param_a = QgsProcessingParameterString(
            self.CONNECTION_NAME_CENTRAL,
            tr('PostgreSQL connection to the central database'),
            defaultValue=connection_name_central,
            optional=False
        )
        db_param_a.setMetadata({
            'widget_wrapper': {
                'class': 'processing.gui.wrappers_postgis.ConnectionWidgetWrapper'
            }
        })
        self.addParameter(db_param_a)

        # Local directory containing the files to send to the clone by FTP
        local_qgis_project_folder = ls.variable('local/qgis_project_folder')
        self.addParameter(
            QgsProcessingParameterFile(
                self.LOCAL_QGIS_PROJECT_FOLDER,
                tr('Local desktop QGIS project folder'),
                defaultValue=local_qgis_project_folder,
                behavior=QgsProcessingParameterFile.Folder,
                optional=False
            )
        )

        # Clone FTP connection parameters
        # host
        clone_ftp_host = ls.variable('ftp:clone/host')
        self.addParameter(
            QgsProcessingParameterString(
                self.CLONE_FTP_HOST,
                tr('Clone FTP Server host'),
                defaultValue=clone_ftp_host,
                optional=False
            )
        )
        # port
        clone_ftp_port = ls.variable('ftp:clone/port')
        self.addParameter(
            QgsProcessingParameterNumber(
                self.CLONE_FTP_PORT,
                tr('Clone FTP Server port'),
                defaultValue=clone_ftp_port,
                optional=False
            )
        )
        # login
        clone_ftp_login = ls.variable('ftp:clone/user')
        self.addParameter(
            QgsProcessingParameterString(
                self.CLONE_FTP_LOGIN,
                tr('Clone FTP Server login'),
                defaultValue=clone_ftp_login,
                optional=False
            )
        )
        # password
        self.addParameter(
            QgsProcessingParameterString(
                self.CLONE_FTP_PASSWORD,
                tr('Clone FTP Server password'),
                optional=True
            )
        )
        # remote directory
        clone_ftp_remote_dir = ls.variable('ftp:clone/remote_directory')
        self.addParameter(
            QgsProcessingParameterString(
                self.CLONE_FTP_REMOTE_DIR,
                tr('Clone FTP Server remote directory'),
                defaultValue=clone_ftp_remote_dir,
                optional=False
            )
        )

        # Exclude some directories from sync
        excluded_directories = ls.variable('local/excluded_directories')
        if not excluded_directories:
            excluded_directories = 'data'
        self.addParameter(
            QgsProcessingParameterString(
                self.FTP_EXCLUDE_REMOTE_SUBDIRS,
                tr('List of sub-directory to exclude from synchro, separated by commas.'),
                defaultValue=excluded_directories,
                optional=True
            )
        )

        # OUTPUTS
        # Add output for message
        self.addOutput(
            QgsProcessingOutputNumber(
                self.OUTPUT_STATUS, tr('Output status')
            )
        )
        self.addOutput(
            QgsProcessingOutputString(
                self.OUTPUT_STRING, tr('Output message')
            )
        )

    def checkParameterValues(self, parameters, context):

        # Check FTP binary
        status, msg = checkFtpBinary()
        if not status:
            return status, msg

        return super(SendProjectsAndFilesToCloneFtp, self).checkParameterValues(parameters, context)

    def processAlgorithm(self, parameters, context, feedback):
        """
        Here is where the processing itself takes place.
        """
        status = 0
        msg = ''
        output = {
            self.OUTPUT_STATUS: status,
            self.OUTPUT_STRING: msg
        }

        # Parameters
        ftphost = parameters[self.CLONE_FTP_HOST]
        ftpport = parameters[self.CLONE_FTP_PORT]
        ftplogin = parameters[self.CLONE_FTP_LOGIN]
        ftppassword = parameters[self.CLONE_FTP_PASSWORD].strip()
        ftpdir = parameters[self.CLONE_FTP_REMOTE_DIR]
        localdir = parameters[self.LOCAL_QGIS_PROJECT_FOLDER]
        excluded_directories = parameters[self.FTP_EXCLUDE_REMOTE_SUBDIRS].strip()

        # store parameters
        ls = lizsyncConfig()
        ls.setVariable('ftp:clone/host', ftphost)
        ls.setVariable('ftp:clone/port', ftpport)
        ls.setVariable('ftp:clone/user', ftplogin)
        ls.setVariable('ftp:clone/password', ftppassword)
        ls.setVariable('ftp:clone/remote_directory', ftpdir)
        ls.setVariable('local/qgis_project_folder', localdir)
        ls.setVariable('local/excluded_directories', excluded_directories)
        ls.save()

        # Check localdir
        feedback.pushInfo(tr('CHECK LOCAL PROJECT DIRECTORY'))
        if not localdir or not os.path.isdir(localdir):
            m = tr('QGIS project local directory not found')
            raise QgsProcessingException(m)
        else:
            m = tr('QGIS project local directory ok')
            feedback.pushInfo(m)

        # Check ftp
        if not ftppassword:
            ok, password, msg = get_ftp_password(ftphost, ftpport, ftplogin)
            if not ok:
                raise QgsProcessingException(msg)
        else:
            password = ftppassword
        ok, msg = check_ftp_connection(ftphost, ftpport, ftplogin, password)
        if not ok:
            raise QgsProcessingException(msg)

        # Check if ftpdir exists
        ok = True
        feedback.pushInfo(tr('CHECK REMOTE DIRECTORY') + ' %s' % ftpdir)
        ftp = FTP()
        ftp.connect(ftphost, ftpport)
        ftp.login(ftplogin, password)
        try:
            ftp.cwd(ftpdir)
            # do the code for successful cd
            m = tr('Remote directory exists in the central server')
            for file_name in ftp.nlst():
                if file_name.endswith('.qgs') or file_name.endswith('.qgs.cfg'):
                    feedback.pushInfo(tr('Delete file') + ' %s' % file_name)
                    ftp.delete(file_name)
        except Exception:
            ok = False
            m = tr('Remote directory does not exist')
        finally:
            ftp.close()
        if not ok:
            raise QgsProcessingException(m)

        # Run FTP sync
        feedback.pushInfo(tr('Local directory') + ' %s' % localdir)
        feedback.pushInfo(tr('FTP directory') + ' %s' % ftpdir)
        direction = 'to'  # we send data TO FTP
        ok, msg = ftp_sync(
            ftphost, ftpport, ftplogin, password,
            localdir, ftpdir, direction,
            excluded_directories, feedback
        )
        if not ok:
            raise QgsProcessingException(msg)

        status = 1
        msg = tr("Synchronization successfull")
        output = {
            self.OUTPUT_STATUS: status,
            self.OUTPUT_STRING: msg
        }
        return output
